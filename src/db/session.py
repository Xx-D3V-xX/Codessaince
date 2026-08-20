"""
session.py — Phase 3 database connectivity.

Single place engine/session construction happens, so every future consumer
(Phase 4's rules engine, Phase 8's API layer, ad hoc scripts) shares one
connection-pooling config instead of each opening its own engine. Postgres
is the target per CLAUDE.md §3.5 ("Real database (Postgres/SQLite confirmed
— Postgres for the actual build)"); DATABASE_URL is read from the
environment (see .env.example) rather than hardcoded, so local dev,
CI, and the judging environment can each point at a different instance
without touching code.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()

DEFAULT_DATABASE_URL = "postgresql+psycopg2://creditgate:creditgate@localhost:5434/creditgate"
DATABASE_URL = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@contextmanager
def get_session() -> Iterator[Session]:
    """one transaction per `with` block — commits on clean exit, rolls back on exception."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
