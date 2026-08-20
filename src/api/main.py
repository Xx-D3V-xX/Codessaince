"""
main.py (src/api/) — FastAPI application entry point for Phase 8.

Run locally with:
    uvicorn src.api.main:app --reload

Interactive API docs (auto-generated from src/api/schemas.py's Pydantic
models — PS-1's own "clear APIs/data contracts between major components"
requirement) are served at /docs once running.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.api.dataset import build_dataset
from src.api.routers import applications, exceptions, rules

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"


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
)

app.include_router(applications.router)
app.include_router(rules.router)
app.include_router(exceptions.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# Phase 9's basic frontend — same-origin static files, mounted last so it
# never shadows the API routes above. Deliberately minimal (plain HTML/JS,
# no build step, no framework) per this phase's own explicit scope: "basic
# frontend for testing purposes, will improve later" — not a production UI.
if FRONTEND_DIR.exists():
    app.mount("/app", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
