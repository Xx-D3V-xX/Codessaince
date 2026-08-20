"""
main.py (src/api/) — FastAPI application entry point for Phase 8.

Run locally with:
    uvicorn src.api.main:app --reload

Backend and frontend are two fully independent processes, deliberately —
see frontend/README.md for how to run the frontend's own static server.
This process previously also mounted frontend/ as a StaticFiles route, but
`uvicorn --reload` watches the WHOLE working directory by default, so any
frontend-only edit (a .js/.html/.css file, nothing backend-related) was
triggering a full process restart — which re-runs the lifespan hook below
and re-computes the ~8,000-applicant dataset from scratch, a ~1-minute
cost, for a change that has nothing to do with the backend. Splitting them
into two processes fixes that structurally: editing the frontend can never
again cause the backend to restart, and vice versa. CORS is enabled below
so the frontend (now serving from its own origin/port) can still call this
API.

/docs is a public, no-login system documentation page (docs/system_docs.html)
— ports, containers, credentials, every endpoint, what was tested and how.
FastAPI's own auto-generated interactive/OpenAPI docs are moved to
/api-reference to make room for it (see docs_url below) — they still exist,
just not at the path a human docs page would more usefully occupy.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse

from src.api.dataset import build_dataset
from src.api.routers import applications, exceptions, rules

DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "docs"


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("loading applicant dataset (Phase 0-2 pipeline) — this runs once at startup, see src/api/dataset.py")
    app.state.dataset = build_dataset()
    print(f"dataset ready: {len(app.state.dataset.master_by_id)} applicants")
    yield


app = FastAPI(
    title="CreditGate API",
    description="Smart Credit Underwriting & Configurable BRE for NBFC Domain — PS-1 submission",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api-reference",
    redoc_url="/api-reference/redoc",
)

# the frontend is a separate process/origin now (see module docstring) --
# credentials aren't used anywhere (no cookies/sessions), so a permissive
# origin list is fine for this local dev/demo setup rather than hardcoding
# one frontend port here too.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(applications.router)
app.include_router(rules.router)
app.include_router(exceptions.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """the bare root has no content of its own — the frontend now lives in its own separate process, see frontend/README.md."""
    return RedirectResponse(url="/docs")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    """no favicon asset -- return an empty 204 instead of letting every browser tab spam 404s in the server log."""
    return Response(status_code=204)


@app.get("/docs", response_class=HTMLResponse, include_in_schema=False)
def system_docs() -> HTMLResponse:
    """
    public, no-login system documentation — everything built, every port
    and service, the docker container, local dev credentials, every API
    endpoint, and what was tested and how. Static content in
    docs/system_docs.html, served here rather than mounted as a StaticFiles
    directory so it can live at exactly /docs (a StaticFiles mount can't
    serve a single file at a non-directory path this cleanly).
    """
    return HTMLResponse((DOCS_DIR / "system_docs.html").read_text(encoding="utf-8"))
