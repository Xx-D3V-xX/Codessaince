// Phase 9 — basic test console, vanilla JS, no build step, no framework.
// Talks to this same server's API (same origin, so no CORS setup needed).

const API = ""; // same origin

// ---------------------------------------------------------------------------
// tabs
// ---------------------------------------------------------------------------
document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
  });
});

function switchTab(name) {
  document.querySelector(`.tab-btn[data-tab="${name}"]`).click();
}

async function api(path, options = {}) {
  // spread options FIRST, then set `headers` explicitly last -- the other
  // order (`{ headers: {...}, ...options }`) lets options.headers silently
  // REPLACE the whole headers object (object spread doesn't deep-merge),
  // which drops Content-Type for every caller that passes its own headers
  // (e.g. the exception approve/reject calls' X-User-Role) and makes
  // FastAPI receive the JSON body as an unparsed string. Caught by
  // exercising the actual exception-approval flow in a browser, not just
  // reading the code.
  const resp = await fetch(API + path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const body = await resp.json().catch(() => null);
  if (!resp.ok) {
    // FastAPI's `detail` is a plain string for HTTPException, but an ARRAY
    // of validation-error objects for a 422 (Pydantic request validation) --
    // Error()'s message must always end up a real string or every
    // `${err.message}` interpolation downstream renders "[object Object]".
    let detail = body && body.detail !== undefined ? body.detail : resp.statusText;
    if (typeof detail !== "string") detail = JSON.stringify(detail);
    const err = new Error(detail);
    err.status = resp.status;
    err.body = body;
    throw err;
  }
  return body;
}

function el(html) {
  const div = document.createElement("div");
  div.innerHTML = html.trim();
  return div.firstChild;
}

// this console echoes user- and DB-sourced strings (applicant_id, rule
// values, notes, error messages, audit before/after payloads) straight
// into innerHTML for simplicity (no framework, no build step -- Phase 9's
// own explicit scope). Escape anything that isn't a value WE constructed
// (badges, numbers we formatted) before interpolating it, so a stray
// "<script>" typed into a form field or stored in the DB can't execute.
function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function safeJson(value) {
  return escapeHtml(JSON.stringify(value));
}

function outcomeBadge(outcome) {
  const safe = escapeHtml(outcome);
  return `<span class="badge outcome-${safe}">${safe}</span>`;
}

function conditionBadge(conditionMet) {
  if (conditionMet === true) return `<span class="badge fail">FIRED</span>`;
  if (conditionMet === false) return `<span class="badge pass">clear</span>`;
  return `<span class="badge unknown">unknown</span>`;
}

// ---------------------------------------------------------------------------
// SUBMIT
// ---------------------------------------------------------------------------
document.getElementById("submit-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const applicant_id = document.getElementById("submit-applicant-id").value.trim();
  const loanAmount = document.getElementById("submit-loan-amount").value;
  const tenure = document.getElementById("submit-tenure").value;

  const payload = { applicant_id };
  if (loanAmount) payload.requested_loan_amount = parseFloat(loanAmount);
  if (tenure) payload.requested_tenure_months = parseInt(tenure, 10);

  const box = document.getElementById("submit-result");
  box.textContent = "submitting...";
  try {
    const result = await api("/applications", { method: "POST", body: JSON.stringify(payload) });
    box.innerHTML = `
      <div>application_id: <code>${escapeHtml(result.application_id)}</code></div>
      <div>status: ${escapeHtml(result.status)}</div>
      <div>${escapeHtml(result.message)}</div>
      <button id="submit-view-decision-btn" class="secondary" style="margin-top:0.5rem">View decision &rarr;</button>
    `;
    document.getElementById("submit-view-decision-btn").addEventListener("click", () => {
      document.getElementById("decision-app-id").value = result.application_id;
      switchTab("decision");
      loadDecision();
    });
  } catch (err) {
    box.innerHTML = `<span class="error">Error: ${escapeHtml(err.message)}</span>`;
  }
});

// ---------------------------------------------------------------------------
// DECISION (+ re-run / side-by-side comparison)
// ---------------------------------------------------------------------------
let lastDecisionSnapshot = null;

function renderTriggeredRules(rules) {
  if (!rules || rules.length === 0) return `<p class="muted">no triggered_rules on this decision</p>`;
  const rows = rules
    .map(
      (r) => `
      <tr>
        <td>${escapeHtml(r.rule_code)} <span class="muted">v${escapeHtml(r.version)}</span></td>
        <td>${escapeHtml(r.field)}</td>
        <td>${escapeHtml(r.operator)}</td>
        <td>${safeJson(r.actual_value)}</td>
        <td>${safeJson(r.threshold)}</td>
        <td>${conditionBadge(r.condition_met)}</td>
        <td>${escapeHtml(r.outcome)}${r.severity ? " / " + escapeHtml(r.severity) : ""}</td>
        <td>${escapeHtml(r.reason_code)}</td>
        <td>${escapeHtml(r.rule_group)}</td>
      </tr>`
    )
    .join("");
  return `
    <table>
      <thead><tr><th>Rule</th><th>Field</th><th>Op</th><th>Actual</th><th>Threshold</th><th>Condition</th><th>Outcome</th><th>Reason</th><th>Group</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function renderDecisionCard(decision) {
  if (!decision.decision_id) {
    return `<div class="card">application_status: ${escapeHtml(decision.application_status)}<br><span class="muted">no decision yet</span></div>`;
  }
  return `
    <div class="card">
      <div>application_status: ${escapeHtml(decision.application_status)} &nbsp; decision_id: <code>${escapeHtml(decision.decision_id)}</code> ${decision.is_current ? "(current)" : "<span class=\"muted\">(superseded)</span>"}</div>
      <div style="margin-top:0.5rem">
        outcome: ${outcomeBadge(decision.outcome)} &nbsp;
        effective_outcome: <strong>${escapeHtml(decision.effective_outcome ?? "-")}</strong>
      </div>
      <div style="margin-top:0.5rem">
        risk_grade: <strong>${escapeHtml(decision.risk_grade ?? "-")}</strong> &nbsp;
        eligible_amount: <strong>${escapeHtml(decision.eligible_amount ?? "-")}</strong> &nbsp;
        interest_rate: <strong>${escapeHtml(decision.interest_rate ?? "-")}</strong>% &nbsp;
        tenure_months: <strong>${escapeHtml(decision.tenure_months ?? "-")}</strong> &nbsp;
        model_risk_score: <strong>${decision.model_risk_score != null ? escapeHtml(decision.model_risk_score.toFixed(4)) : "-"}</strong>
      </div>
      ${decision.exception ? `<div style="margin-top:0.5rem">exception: <strong>${escapeHtml(decision.exception.level)}</strong> / ${escapeHtml(decision.exception.status)} (assigned_to: ${escapeHtml(decision.exception.assigned_to ?? "-")})</div>` : ""}
      <div style="margin-top:0.75rem">${renderTriggeredRules(decision.triggered_rules)}</div>
    </div>`;
}

async function loadDecision() {
  const appId = document.getElementById("decision-app-id").value.trim();
  const box = document.getElementById("decision-current");
  document.getElementById("decision-comparison").innerHTML = "";
  if (!appId) { box.innerHTML = `<span class="error">enter an application_id</span>`; return; }
  box.textContent = "loading...";
  try {
    const decision = await api(`/applications/${appId}/decision`);
    lastDecisionSnapshot = decision;
    box.innerHTML = renderDecisionCard(decision);
    document.getElementById("decision-rerun-btn").disabled = false;
  } catch (err) {
    box.innerHTML = `<span class="error">Error: ${escapeHtml(err.message)}</span>`;
  }
}
document.getElementById("decision-load-btn").addEventListener("click", loadDecision);

async function pollDecision(appId, timeoutMs = 20000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const decision = await api(`/applications/${appId}/decision`);
    if (decision.application_status === "DECISIONED" || decision.application_status === "FAILED") return decision;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("timed out waiting for the re-run to finish");
}

function diff(before, after, label) {
  const same = before === after;
  return `<div>${escapeHtml(label)}: ${escapeHtml(before ?? "-")} ${same ? "" : `<span class="diff">&rarr; ${escapeHtml(after ?? "-")}</span>`}</div>`;
}

document.getElementById("decision-rerun-btn").addEventListener("click", async () => {
  const appId = document.getElementById("decision-app-id").value.trim();
  const comparisonBox = document.getElementById("decision-comparison");
  if (!appId || !lastDecisionSnapshot) return;
  const before = lastDecisionSnapshot;
  comparisonBox.innerHTML = `<p class="muted">re-running against current rules...</p>`;
  try {
    await api(`/applications/${appId}/rerun`, { method: "POST" });
    const after = await pollDecision(appId);
    document.getElementById("decision-current").innerHTML = renderDecisionCard(after);
    lastDecisionSnapshot = after;

    comparisonBox.innerHTML = `
      <h3>Before &rarr; after re-run</h3>
      <div class="comparison">
        <div class="card">
          <h3>Before (decision ${escapeHtml(before.decision_id)})</h3>
          ${diff(before.outcome, after.outcome, "outcome")}
          ${diff(before.risk_grade, after.risk_grade, "risk_grade")}
          ${diff(before.eligible_amount, after.eligible_amount, "eligible_amount")}
          ${diff(before.interest_rate, after.interest_rate, "interest_rate")}
        </div>
        <div class="card">
          <h3>After (decision ${escapeHtml(after.decision_id)})</h3>
          ${diff(after.outcome, before.outcome, "outcome (same field, other direction)")}
          <p class="muted">full detail is in the "current" card above — this panel exists to make the before/after change obvious at a glance for the threshold-edit-and-re-run demo flow.</p>
        </div>
      </div>`;
  } catch (err) {
    comparisonBox.innerHTML = `<span class="error">Error: ${escapeHtml(err.message)}</span>`;
  }
});

// ---------------------------------------------------------------------------
// RULES ADMIN
// ---------------------------------------------------------------------------
function renderRulesTable(rules, editedByFieldId) {
  const rows = rules
    .map(
      (r) => `
      <tr data-rule-code="${escapeHtml(r.rule_code)}">
        <td>${escapeHtml(r.rule_code)}</td>
        <td>v${escapeHtml(r.version)}</td>
        <td>${escapeHtml(r.field)}</td>
        <td>${escapeHtml(r.operator)}</td>
        <td><input class="value-input" style="min-width:160px" value="${escapeHtml(JSON.stringify(r.value))}"></td>
        <td>${escapeHtml(r.outcome)}${r.severity ? " / " + escapeHtml(r.severity) : ""}</td>
        <td>${escapeHtml(r.priority)}</td>
        <td>${escapeHtml(r.rule_group)}</td>
        <td><button class="secondary save-rule-btn">Save</button> <span class="save-status muted"></span></td>
      </tr>`
    )
    .join("");
  const table = el(`
    <table>
      <thead><tr><th>rule_code</th><th>ver</th><th>field</th><th>op</th><th>value (JSON, editable)</th><th>outcome</th><th>prio</th><th>group</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`);

  table.querySelectorAll(".save-rule-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const tr = btn.closest("tr");
      const ruleCode = tr.dataset.ruleCode;
      const input = tr.querySelector(".value-input");
      const status = tr.querySelector(".save-status");
      const editedBy = document.getElementById(editedByFieldId).value.trim() || "demo_admin";
      let value;
      try {
        value = JSON.parse(input.value);
      } catch (e) {
        status.innerHTML = `<span class="error">invalid JSON</span>`;
        return;
      }
      status.textContent = "saving...";
      try {
        const updated = await api(`/rules/${ruleCode}`, { method: "PATCH", body: JSON.stringify({ edited_by: editedBy, value }) });
        status.innerHTML = `<span style="color:var(--ok)">saved as v${updated.version}</span>`;
        tr.children[1].textContent = `v${updated.version}`;
      } catch (err) {
        status.innerHTML = `<span class="error">${escapeHtml(err.message)}</span>`;
      }
    });
  });

  return table;
}

document.getElementById("rules-load-btn").addEventListener("click", async () => {
  const pipeline = document.getElementById("rules-pipeline").value;
  const box = document.getElementById("rules-table");
  box.textContent = "loading...";
  try {
    const rules = await api(`/rules?pipeline=${pipeline}`);
    box.innerHTML = "";
    box.appendChild(renderRulesTable(rules, "rules-edited-by"));
  } catch (err) {
    box.innerHTML = `<span class="error">Error: ${escapeHtml(err.message)}</span>`;
  }
});

// ---------------------------------------------------------------------------
// EXCEPTION QUEUE
// ---------------------------------------------------------------------------
async function loadExceptionQueue() {
  const level = document.getElementById("exc-level").value;
  const resolvedByFieldId = "exc-resolved-by";
  const box = document.getElementById("exc-table");
  box.textContent = "loading...";
  try {
    const rows = await api(`/exceptions?level=${level}&status=PENDING`);
    if (rows.length === 0) {
      box.innerHTML = `<p class="muted">no pending ${level} exceptions</p>`;
      return;
    }
    const trs = rows
      .map(
        (r) => `
        <tr data-exception-id="${escapeHtml(r.id)}">
          <td>${escapeHtml(r.applicant_id)}</td>
          <td><code>${escapeHtml(r.application_id)}</code></td>
          <td>${outcomeBadge(r.decision_outcome)}</td>
          <td>${escapeHtml(r.risk_grade ?? "-")}</td>
          <td>${escapeHtml(r.eligible_amount ?? "-")}</td>
          <td>${escapeHtml(r.assigned_to ?? "-")}</td>
          <td>${escapeHtml(new Date(r.created_at).toLocaleString())}</td>
          <td>
            <button class="approve-btn">Approve</button>
            <button class="secondary reject-btn">Reject</button>
            <div class="action-status muted"></div>
          </td>
        </tr>`
      )
      .join("");
    box.innerHTML = "";
    box.appendChild(
      el(`<table>
        <thead><tr><th>applicant</th><th>application_id</th><th>outcome</th><th>grade</th><th>eligible</th><th>assigned</th><th>routed</th><th>action</th></tr></thead>
        <tbody>${trs}</tbody>
      </table>`)
    );

    box.querySelectorAll(".approve-btn, .reject-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const tr = btn.closest("tr");
        const exceptionId = tr.dataset.exceptionId;
        const action = btn.classList.contains("approve-btn") ? "approve" : "reject";
        const status = tr.querySelector(".action-status");
        const resolvedBy = document.getElementById(resolvedByFieldId).value.trim() || "demo_reviewer";
        // read the role LIVE at click time, same as resolvedBy above --
        // `role` from the outer loadExceptionQueue() scope is a snapshot
        // from when the queue was loaded, so switching the role dropdown
        // afterward (without reloading) would silently keep using the old
        // one if read from that closure instead.
        const currentRole = document.getElementById("exc-role").value;
        status.textContent = "submitting...";
        try {
          await api(`/exceptions/${exceptionId}/${action}`, {
            method: "POST",
            headers: { "X-User-Role": currentRole },
            body: JSON.stringify({ resolved_by: resolvedBy }),
          });
          status.innerHTML = `<span style="color:var(--ok)">${action}d</span>`;
          setTimeout(loadExceptionQueue, 600);
        } catch (err) {
          status.innerHTML = `<span class="error">${err.status === 403 ? "forbidden — wrong role for this level" : escapeHtml(err.message)}</span>`;
        }
      });
    });
  } catch (err) {
    box.innerHTML = `<span class="error">Error: ${escapeHtml(err.message)}</span>`;
  }
}
document.getElementById("exc-load-btn").addEventListener("click", loadExceptionQueue);

// ---------------------------------------------------------------------------
// AUDIT
// ---------------------------------------------------------------------------
document.getElementById("audit-load-btn").addEventListener("click", async () => {
  const appId = document.getElementById("audit-app-id").value.trim();
  const box = document.getElementById("audit-table");
  if (!appId) { box.innerHTML = `<span class="error">enter an application_id</span>`; return; }
  box.textContent = "loading...";
  try {
    const audit = await api(`/applications/${appId}/audit`);
    if (audit.entries.length === 0) {
      box.innerHTML = `<p class="muted">no audit entries yet</p>`;
      return;
    }
    const rows = audit.entries
      .map(
        (e) => `
        <tr>
          <td>${escapeHtml(new Date(e.timestamp).toLocaleString())}</td>
          <td>${escapeHtml(e.actor)}</td>
          <td>${escapeHtml(e.action)}</td>
          <td>${escapeHtml(e.entity_type)}</td>
          <td><code>${escapeHtml(e.entity_id)}</code></td>
          <td><pre style="margin:0;white-space:pre-wrap">${e.before ? safeJson(e.before) : "-"}</pre></td>
          <td><pre style="margin:0;white-space:pre-wrap">${e.after ? safeJson(e.after) : "-"}</pre></td>
        </tr>`
      )
      .join("");
    box.innerHTML = `<table>
      <thead><tr><th>time</th><th>actor</th><th>action</th><th>entity_type</th><th>entity_id</th><th>before</th><th>after</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  } catch (err) {
    box.innerHTML = `<span class="error">Error: ${escapeHtml(err.message)}</span>`;
  }
});
