# CreditGate Frontend (v2)

A new Next.js frontend integrating the `digi-pay` marketing shell and the
`credit-gate` dashboard shell against the real `Xx-D3V-xX/Codessaince`
FastAPI backend. Built in a fresh directory per the handoff instructions --
the backend repo's own `frontend/` (Phase 9 plain-HTML console) is untouched
and still works as a fallback/reference.

## Running it

```bash
pnpm install
pnpm dev
```

Opens on `http://localhost:3000`. The backend must be running separately:

```bash
# in the Codessaince repo
uvicorn src.api.main:app --reload
```

If the backend isn't on `localhost:8000`, either set
`NEXT_PUBLIC_API_BASE_URL` in `.env.local` (copy `.env.local.example`) or
override per-tab with `?api=http://host:port` in the browser URL -- same
convention the existing `frontend/app.js` uses.

## Flow

`/` -- digi-pay homepage. "Log in" / "Get started" opens the original login
modal, repurposed as a **role select** (no auth exists on the backend --
see `src/api/deps.py`'s own docstring). Picking a role stores it in
`localStorage` and routes to `/dashboard`.

`/dashboard` -- the credit-gate shell, wired to the 5 priority features:

1. **Applications \u2192 New application** -- picks a real applicant via
   `GET /applicants`, submits via `POST /applications`.
2. **Overview** -- polls `GET /applications/{id}/decision`, renders the
   rule trace (three-state fired/clear/unknown, never coerced), and wires
   `POST /applications/{id}/rerun` with a before/after comparison.
3. **Rule engine** -- `GET /rules?pipeline=` per-pipeline table,
   `PATCH /rules/{rule_code}` live threshold edits.
4. **Exceptions** -- `GET /exceptions?level=&status=`, approve/reject with
   `X-User-Role` set from the selected role; queue is filtered client-side
   to the levels that role can act on (server still enforces the real
   check).
5. **Audit trail** -- `GET /applications/{id}/audit`, expandable
   before/after diffs per entry.

A header dropdown lets you switch roles without leaving the dashboard
(added beyond the README's literal modal-only flow, by request, for easier
testing across all three roles).

## Known gaps / deliberate simplifications

- **No `GET /applications` list endpoint exists on the backend.**
  Only `.../decision` and `.../audit` exist, both keyed by an id you
  already have. There's no way to ask the backend "what's been submitted."
  Worked around with a client-side, `localStorage`-backed tracker
  (`lib/tracked-applications-context.tsx`) that records every successful
  submission from *this browser*. The "Applications" table reflects that,
  not a durable server-side history -- a different browser or a cleared
  localStorage genuinely won't see past submissions. If a real list
  endpoint gets added to the backend, swap this tracker's read side for a
  fetch and the rest of the UI (list, click-to-select) doesn't need to change.
- **`Rules` admin's inline editor exposes a single `"threshold"` key.**
  Every seeded rule's `value` dict uses that shape (`{"threshold": X}`).
  If a rule ever needs a different `value` shape (e.g. a list for an `IN`
  operator), the editor's `buildValuePatch()` in
  `components/dashboard/rules-admin.tsx` falls back to treating the input
  as raw JSON, but hasn't been exercised against that case -- check it
  against `src/rules/seed_rules.py`'s actual seeded rules if editing a
  non-numeric-threshold rule live.
- **Not yet wired:** `src/api/routers/applicants.py`'s preview endpoint
  (browse-and-preview without submitting) beyond the minimal picker in
  Intake. Onboarding (`src/api/routers/onboarding.py`) IS now wired --
  identity verify, consent, the 4-scenario synthetic fetch, and status/SHAP
  polling all work end-to-end via Applications \u2192 New account.
- **Onboarding is a fixed 4-scenario generator, not free-form applicant
  creation.** `section` (accept/reject/l1/l2) guarantees the outcome; the
  declared factors (income, age, obligations, etc.) get folded into a
  profile synthesized to still land there -- entering wildly inconsistent
  factors for a given scenario won't override the guaranteed outcome. This
  matches the backend's own documented behavior, not a frontend limitation.
- **CSS scoping:** `digi-pay` and `credit-gate` each originally defined
  color tokens on `:root`, which collided when merged into one app. Fixed
  by scoping each shell's tokens under `.marketing-scope` /
  `.dashboard-scope` wrapper classes (see the top of `app/globals.css` for
  the full rationale) rather than on `:root` directly -- if either shell's
  design gets revisited, keep new tokens scoped the same way.

## Not yet run against a live backend

This was built and type-checked (`tsc --noEmit` passes clean, `pnpm build`
succeeds) in an environment without access to a running Postgres/backend
instance or to `fonts.googleapis.com`, so it hasn't been exercised against
real HTTP traffic yet. Read every request/response shape against
`src/api/schemas.py` again if something doesn't line up on first run --
particularly the polling timing in `lib/api.ts`'s `pollDecision()` (700ms
interval, 30s timeout; the backend's own `frontend/app.js` reference uses
500ms/20s, adjust either if evaluation is taking longer in practice).
