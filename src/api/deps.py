"""
deps.py — FastAPI dependencies: a request-scoped DB session, the
applicant-dataset cache (built once at startup, see main.py's lifespan),
and role-gating for exception approval endpoints.

**Role-gating is a demo-grade stand-in, disclosed as such, not real
authentication/authorization.** There is no user/auth system anywhere in
this project's scope (CLAUDE.md never asks for one) — a real deployment
would sit this behind a proper identity provider. What's implemented here
is genuine, testable authorization LOGIC (an L1 exception requires an
L1-or-higher role, a CREDIT_HEAD exception requires exactly CREDIT_HEAD,
etc.) driven by a plain `X-User-Role` header, so the role-gating behavior
itself is real and verifiable — it's only the *identity* side (proving the
header wasn't forged) that's out of scope.
"""

from __future__ import annotations

from typing import Iterator

from fastapi import Header, HTTPException, Request

from sqlalchemy.orm import Session

from src.api.dataset import ApplicantDataset
from src.db.models import ExceptionLevel
from src.db.session import SessionLocal

# mirrors src/rules/exceptions.py's QUEUE_BY_LEVEL — CREDIT_HEAD can act on
# any level (it's the top of the escalation chain), a level-specific role
# can only act on its own level. "admin" is an additional top-of-chain
# role alongside credit_head (not present in QUEUE_BY_LEVEL itself, which
# only names credit_head as the CREDIT_HEAD queue's assignee) — it can act
# on any level with the same authority as credit_head.
_ROLES_ALLOWED_FOR_LEVEL: dict[ExceptionLevel, set[str]] = {
    ExceptionLevel.L1: {"credit_ops_l1", "credit_head", "admin"},
    ExceptionLevel.L2: {"credit_ops_l2", "credit_head", "admin"},
    ExceptionLevel.CREDIT_HEAD: {"credit_head", "admin"},
}


def get_db() -> Iterator[Session]:
    """one session per request — commits on clean completion, rolls back on any exception, always closes."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_dataset(request: Request) -> ApplicantDataset:
    return request.app.state.dataset


def require_role_for_level(level: ExceptionLevel, x_user_role: str | None) -> None:
    allowed = _ROLES_ALLOWED_FOR_LEVEL[level]
    if x_user_role not in allowed:
        raise HTTPException(
            status_code=403,
            detail=f"role {x_user_role!r} is not authorized to resolve a {level.value} exception (requires one of {sorted(allowed)})",
        )


def user_role_header(x_user_role: str | None = Header(default=None)) -> str | None:
    return x_user_role
