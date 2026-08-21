"""
test_api.py — Phase 8 verification: real HTTP requests (FastAPI TestClient,
in-process ASGI, real request/response cycle) against a live Postgres
instance and the real dataset, same standard of evidence as every prior
phase.

Covers every TODO.md Phase 8 bullet:
  1. POST /applications -> 202 + application_id, async saga (background task)
  2. GET /applications/{id}/decision -> poll for result
  3. PATCH /rules/{rule_code} -> live threshold edit
  4. POST /applications/{id}/rerun -> re-evaluate stored profile
  5. GET /applications/{id}/audit -> full audit trail
  6. Exception approval endpoints, role-gated (403 without the right role,
     200 with it)
"""

import time

from fastapi.testclient import TestClient

from src.api.main import app
from src.db.models import ApplicantPipeline
from src.db.session import get_session
from src.rules.context import build_rule_context, is_insufficient_data, pipeline_for
from src.rules.crud import active_rules_for_pipeline
from src.rules.evaluator import evaluate_rule_groups, evaluate_rules
from src.rules.resolver import resolve_decision


def poll_decision(client: TestClient, application_id: str, timeout_s: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        resp = client.get(f"/applications/{application_id}/decision")
        assert resp.status_code == 200
        body = resp.json()
        if body["application_status"] in ("DECISIONED", "FAILED"):
            return body
        time.sleep(0.5)
    raise TimeoutError(f"application {application_id} did not reach a terminal status within {timeout_s}s")


print("=== starting API (triggers dataset build via lifespan -- this takes a minute) ===")
with TestClient(app) as client:
    print("\n=== health check ===")
    resp = client.get("/health")
    assert resp.status_code == 200 and resp.json() == {"status": "ok"}
    print("assert: /health -- PASS")

    print("\n=== finding a real STP candidate and a real L1-exception candidate (pre-computed, not via the API) ===")
    dataset = app.state.dataset

    with get_session() as s:
        ind_rules = active_rules_for_pipeline(s, ApplicantPipeline.INDIVIDUAL)

    stp_candidate = l1_candidate = None
    for applicant_id, master_row in dataset.master_by_id.items():
        if master_row["applicant_type"] not in ("SALARIED", "SELF_EMPLOYED"):
            continue
        found = dataset.get(applicant_id)
        if found is None:
            continue
        master_row, feature_row, bureau_row, bank_row = found
        context = build_rule_context(master_row, feature_row, bureau_row=bureau_row, bank_row=bank_row)
        if is_insufficient_data(context):
            continue
        results = evaluate_rules(ind_rules, context)
        group_results = evaluate_rule_groups(results)
        resolved = resolve_decision(group_results, insufficient_data=False)
        if resolved.outcome.value == "STP_APPROVED" and stp_candidate is None:
            stp_candidate = applicant_id
        elif resolved.outcome.value == "EXCEPTION_REQUIRED" and resolved.severity and resolved.severity.value == "L1" and l1_candidate is None:
            l1_candidate = applicant_id
        if stp_candidate and l1_candidate:
            break
    assert stp_candidate and l1_candidate
    print(f"STP candidate: {stp_candidate}, L1 candidate: {l1_candidate}")

    # -------------------------------------------------------------------
    # 1+2: POST /applications (async saga) + GET .../decision (poll)
    # -------------------------------------------------------------------
    print("\n=== 1+2: POST /applications + poll GET .../decision ===")
    resp = client.post("/applications", json={"applicant_id": stp_candidate})
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "RECEIVED"
    application_id = body["application_id"]
    print(f"submitted: {body}")

    decision = poll_decision(client, application_id)
    print(f"polled result: outcome={decision['outcome']}, risk_grade={decision['risk_grade']}, eligible_amount={decision['eligible_amount']}")
    assert decision["application_status"] == "DECISIONED"
    assert decision["outcome"] == "STP_APPROVED"
    assert decision["eligible_amount"] is not None
    assert decision["triggered_rules"] is not None and len(decision["triggered_rules"]) > 0
    print("assert: POST 202 + async background evaluation + GET poll returns a real, priced decision -- PASS")

    # -------------------------------------------------------------------
    # 3: PATCH /rules/{rule_code}
    # -------------------------------------------------------------------
    print("\n=== 3: PATCH /rules/IND_MIN_BUREAU_SCORE (live threshold edit) ===")
    resp = client.get("/rules/IND_MIN_BUREAU_SCORE")
    assert resp.status_code == 200
    original_version = resp.json()["version"]
    original_value = resp.json()["value"]
    print(f"current: v{original_version}, value={original_value}")

    resp = client.patch("/rules/IND_MIN_BUREAU_SCORE", json={"edited_by": "test_api_judge", "value": {"threshold": 601}})
    assert resp.status_code == 200, resp.text
    patched = resp.json()
    assert patched["version"] == original_version + 1
    assert patched["value"] == {"threshold": 601}
    print(f"patched: v{patched['version']}, value={patched['value']}")
    print("assert: PATCH created a new version with the new threshold live -- PASS")

    # revert immediately
    resp = client.patch("/rules/IND_MIN_BUREAU_SCORE", json={"edited_by": "test_api_judge", "value": original_value})
    assert resp.status_code == 200
    print(f"reverted to: v{resp.json()['version']}, value={resp.json()['value']}")

    # -------------------------------------------------------------------
    # 4: POST /applications/{id}/rerun
    # -------------------------------------------------------------------
    print("\n=== 4: POST /applications/{id}/rerun ===")
    resp = client.post(f"/applications/{application_id}/rerun")
    assert resp.status_code == 202, resp.text
    print(f"rerun triggered: {resp.json()}")

    rerun_decision = poll_decision(client, application_id)
    assert rerun_decision["application_status"] == "DECISIONED"
    assert rerun_decision["decision_id"] != decision["decision_id"], "rerun should have produced a new, distinct decision"
    print(f"rerun produced a new decision: {rerun_decision['decision_id']} (was {decision['decision_id']})")
    print("assert: rerun re-evaluated the stored profile and produced a new chained decision -- PASS")

    # -------------------------------------------------------------------
    # 5: GET /applications/{id}/audit
    # -------------------------------------------------------------------
    print("\n=== 5: GET /applications/{id}/audit ===")
    resp = client.get(f"/applications/{application_id}/audit")
    assert resp.status_code == 200
    audit = resp.json()
    actions = [e["action"] for e in audit["entries"]]
    print(f"{len(audit['entries'])} audit entries: {actions}")
    assert "DECISION_MADE" in actions
    assert audit["entries"] == sorted(audit["entries"], key=lambda e: e["timestamp"]), "audit trail should be chronologically ordered"
    print("assert: full, chronologically-ordered audit trail retrieved for the application -- PASS")

    # -------------------------------------------------------------------
    # 6: exception approval endpoints, role-gated
    # -------------------------------------------------------------------
    print("\n=== 6: exception approval endpoints, role-gated ===")
    resp = client.post("/applications", json={"applicant_id": l1_candidate})
    assert resp.status_code == 202
    exc_application_id = resp.json()["application_id"]
    exc_decision = poll_decision(client, exc_application_id)
    assert exc_decision["outcome"] == "EXCEPTION_REQUIRED"
    exception_id = exc_decision["exception"]["id"]
    assert exc_decision["exception"]["level"] == "L1"
    print(f"routed exception {exception_id} at level {exc_decision['exception']['level']}")

    # wrong role -> 403
    resp = client.post(f"/exceptions/{exception_id}/approve", json={"resolved_by": "someone"}, headers={"X-User-Role": "credit_ops_l2"})
    assert resp.status_code == 403, resp.text
    print(f"L2 role attempting to approve an L1 exception: {resp.status_code} (correctly forbidden)")

    # correct role -> 200
    resp = client.post(f"/exceptions/{exception_id}/approve", json={"resolved_by": "credit_ops_reviewer", "notes": "approved via API test"},
                        headers={"X-User-Role": "credit_ops_l1"})
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["exception"]["status"] == "APPROVED"
    print(f"L1 role approving its own L1 exception: {resp.status_code}, exception status={result['exception']['status']}")
    print("assert: role-gating correctly rejects the wrong role and accepts the right one -- PASS")

    # -------------------------------------------------------------------
    # 6b: "admin" role has credit_head-equivalent authority at every level
    # -------------------------------------------------------------------
    print("\n=== 6b: admin role authorized at all three exception levels ===")
    from fastapi import HTTPException

    from src.api.deps import require_role_for_level
    from src.db.models import ExceptionLevel

    for level in (ExceptionLevel.L1, ExceptionLevel.L2, ExceptionLevel.CREDIT_HEAD):
        require_role_for_level(level, "admin")  # must not raise
        print(f"admin authorized for {level.value} -- PASS")

    # unrelated/invalid role strings are still rejected at every level, unchanged
    for level in (ExceptionLevel.L1, ExceptionLevel.L2, ExceptionLevel.CREDIT_HEAD):
        for bad_role in ("not_a_real_role", None):
            try:
                require_role_for_level(level, bad_role)
                raise AssertionError(f"expected role {bad_role!r} to be rejected for {level.value}")
            except HTTPException as e:
                assert e.status_code == 403
        print(f"invalid/unrelated roles still rejected for {level.value} -- PASS")
    print("assert: admin has credit_head-equivalent authority at every exception level, existing 403 behavior unchanged -- PASS")

print("\nALL PHASE 8 ASSERTIONS PASSED")
