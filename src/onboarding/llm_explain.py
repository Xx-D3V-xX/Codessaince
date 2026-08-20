"""
llm_explain.py — turns the already-computed, already-honest explanation
(fired rules + SHAP feature contributions from src/api/routers/onboarding.py)
into a plain-English paragraph via Gemini, for the frontend to display
alongside — never instead of — the underlying rule trace / reason codes /
SHAP values.

**Deliberately additive, never load-bearing.** Every field this module
reads already exists and is already correct before this module runs — see
DemoStatusResponse.top_reasons / reason_codes / shap_explanation in
routers/onboarding.py. This module's ONLY job is rephrasing what's already
there into a sentence a non-technical judge can read at a glance; it never
computes a new fact, never changes an outcome, and its failure (missing
API key, network error, malformed LLM response) must never break the
underlying, already-working explanation. If the call fails for any reason,
this returns None and the caller falls back to the structured reasons
alone — the API's core correctness never depends on an LLM being reachable.

**API key**: read from the GEMINI_API_KEY environment variable via
python-dotenv, same convention as src/db/session.py's DATABASE_URL — see
that module's docstring. Add GEMINI_API_KEY=<your key> to your local .env
file (copy .env.example if you don't have one yet); nothing in this
repo's source needs editing to supply the real key.
"""

from __future__ import annotations

import os

import httpx
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
# gemini-flash-latest: Google's own rolling alias for the current
# recommended flash-tier model (per their own current sample requests) --
# used instead of a dated model id like gemini-2.5-flash specifically so
# this doesn't go stale again the next time Google retires a specific
# dated version (gemini-2.0-flash was retired 2026-06-01, which is what
# broke the first version of this integration). Override via GEMINI_MODEL
# env var if a specific pinned version is ever preferred instead.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
_TIMEOUT_SECONDS = 8.0  # short — this sits in the request path of a poll the judge is watching live


def _build_prompt(
    outcome: str,
    effective_outcome: str,
    risk_grade: str | None,
    eligible_amount: float | None,
    interest_rate: float | None,
    reason_codes: list[str],
    top_reasons: list[str],
) -> str:
    """
    the prompt is deliberately built ONLY from already-computed facts (the
    same reason_codes/top_reasons the API already returns) — never from raw
    applicant PII beyond what's already in those strings, and never asked
    to invent a justification independent of what the rules engine/SHAP
    already concluded. This keeps the LLM's role strictly to rephrasing,
    not re-deciding.
    """
    facts = "\n".join(f"- {r}" for r in top_reasons) or "- no specific rule or model factor stood out; this was a routine case"
    return (
        "You are writing a short, plain-English explanation of a loan underwriting decision "
        "for a non-technical audience (e.g. a judge watching a live demo). "
        "Do not invent any fact not given below. Do not mention 'SHAP', 'rule_code', or other "
        "technical jargon by name — translate them into plain language instead. "
        "Keep it to 3-5 sentences.\n\n"
        f"Decision outcome: {outcome} (effective status: {effective_outcome})\n"
        + (f"Risk grade: {risk_grade}\n" if risk_grade else "")
        + (f"Eligible amount: {eligible_amount}\n" if eligible_amount is not None else "")
        + (f"Interest rate: {interest_rate}%\n" if interest_rate is not None else "")
        + f"Reason codes triggered: {', '.join(reason_codes) if reason_codes else 'none'}\n"
        + f"Underlying factors (from the rules engine and/or the risk model):\n{facts}\n\n"
        "Write the explanation now, as if speaking directly to the applicant or a reviewer."
    )


def generate_plain_english_explanation(
    outcome: str,
    effective_outcome: str,
    risk_grade: str | None,
    eligible_amount: float | None,
    interest_rate: float | None,
    reason_codes: list[str],
    top_reasons: list[str],
) -> str | None:
    """
    returns a plain-English paragraph, or None if GEMINI_API_KEY isn't set
    or the call fails for any reason — callers must treat None as "not
    available right now", not as an error to surface to the judge mid-demo.
    """
    if not GEMINI_API_KEY:
        return None

    prompt = _build_prompt(outcome, effective_outcome, risk_grade, eligible_amount, interest_rate, reason_codes, top_reasons)

    try:
        response = httpx.post(
            GEMINI_URL,
            headers={"x-goog-api-key": GEMINI_API_KEY},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.2,
                    "maxOutputTokens": 1024,
                    # this task is a short rephrasing job, not multi-step
                    # reasoning -- gemini-flash-latest currently resolves to
                    # a 3.x-generation model that spends part of its output
                    # budget on internal "thinking" tokens before the visible
                    # answer (confirmed via a raw test call: 147 of ~160
                    # total tokens were thoughtsTokenCount, not visible text)
                    # -- thinkingBudget=0 disables that for this call, so the
                    # full token budget goes to the actual answer instead of
                    # being silently consumed by invisible reasoning.
                    "thinkingConfig": {"thinkingBudget": 0},
                },
            },
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        candidates = data.get("candidates") or []
        if not candidates:
            return None
        candidate = candidates[0]
        parts = candidate.get("content", {}).get("parts") or []
        if not parts:
            return None
        # concatenate ALL non-thought parts, not just parts[0] -- Gemini can
        # split one response across multiple parts, and thinking models may
        # also include a separate "thought" part alongside the real answer
        # (excluded here via the `thought` flag) -- reading only parts[0]
        # without this filter was the original bug: it silently truncated
        # the response mid-sentence, observed as a fragment starting
        # mid-word rather than a short-but-complete answer.
        text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
        if not text:
            return None
        finish_reason = candidate.get("finishReason")
        if finish_reason == "MAX_TOKENS":
            # genuinely truncated by the token budget, not a parsing bug --
            # still return what we have (better than nothing for a live
            # demo) but this is a real, different signal worth knowing about
            # if it recurs: raise maxOutputTokens below rather than assume
            # a parts[] bug again.
            print(f"[llm_explain] response truncated by MAX_TOKENS ({len(text)} chars returned)")
        return text
    except Exception as exc:  # noqa: BLE001 -- any failure here (network, auth, malformed response) must degrade to None, never raise into the request path
        # TEMPORARY diagnostic print -- surfaces the real cause in the uvicorn
        # terminal while wiring this up for the first time, since the function
        # must still return None (not raise) either way. Safe to remove once
        # the integration is confirmed working end-to-end.
        print(f"[llm_explain] Gemini call failed: {type(exc).__name__}: {exc}")
        return None
