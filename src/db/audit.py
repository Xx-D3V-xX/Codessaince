"""
audit.py — the one shared audit_log write path TODO.md's Phase 3 checklist
calls for ("one shared write path, not per-endpoint ad hoc writes").

Every state-changing action anywhere in the system — a rule edit, a decision
being recorded, an exception being resolved, a recalibration offset changing
— should call write_audit_log() rather than INSERT-ing into audit_log
directly, so the audit trail's shape can't drift table-by-table.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from src.db.models import AuditLog


def write_audit_log(
    session: Session,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    before: dict | None = None,
    after: dict | None = None,
) -> AuditLog:
    """appends one audit_log row within the caller's transaction — does not commit."""
    entry = AuditLog(
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before,
        after=after,
    )
    session.add(entry)
    return entry
