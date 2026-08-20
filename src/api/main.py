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

from fastapi import FastAPI

from src.api.dataset import build_dataset
from src.api.routers import applications, exceptions, rules


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
