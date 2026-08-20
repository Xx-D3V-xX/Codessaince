"""
main.py (src/api/) — FastAPI application entry point for Phase 8.

Run locally with:
    uvicorn src.api.main:app --reload

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
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from src.api.dataset import build_dataset
from src.api.routers import applications, exceptions, rules

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
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

app.include_router(applications.router)
app.include_router(rules.router)
app.include_router(exceptions.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """the bare root has no content of its own — send a browser landing here to the test console."""
    return RedirectResponse(url="/app")


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


# Phase 9's basic frontend — same-origin static files, mounted last so it
# never shadows the API routes above. Deliberately minimal (plain HTML/JS,
# no build step, no framework) per this phase's own explicit scope: "basic
# frontend for testing purposes, will improve later" — not a production UI.
if FRONTEND_DIR.exists():
    app.mount("/app", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
