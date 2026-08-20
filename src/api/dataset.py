"""
dataset.py — Phase 8's applicant-data cache.

Phases 0-2's pipeline (adapter -> FeatureEngine -> cross_source) processes
all 8,000 generated applicants as one batch, taking on the order of a
minute -- far too slow to re-run per HTTP request. This module runs it
ONCE (at API startup, see src/api/main.py's lifespan) and caches the
result in-process, keyed by applicant_id, for O(1) lookup per request.

**Known, deliberate scaling limitation, not an oversight**: a real
production system would replace this with a proper feature store / online
feature-serving layer (compute once at ingestion time, persist, serve by
key) rather than an in-memory full-batch preload — PS-1 explicitly asks
teams to describe how their MVP would handle higher volumes; this is the
honest answer for this piece: swap this module's implementation for a
feature-store read, nothing above it (routers, evaluate_route_and_price(),
etc.) would need to change, since they already consume it as a
by-applicant-id lookup, not a batch.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ApplicantDataset:
    master_by_id: dict[str, dict]
    bureau_by_id: dict[str, dict]
    bank_by_id: dict[str, dict]
    vector_by_id: dict[str, dict]

    def get(self, applicant_id: str) -> tuple[dict, dict, dict | None, dict | None] | None:
        """(master_row, feature_vector_row, bureau_row, bank_row) or None if applicant_id isn't known."""
        master_row = self.master_by_id.get(applicant_id)
        vector_row = self.vector_by_id.get(applicant_id)
        if master_row is None or vector_row is None:
            return None
        return master_row, vector_row, self.bureau_by_id.get(applicant_id), self.bank_by_id.get(applicant_id)


def build_dataset() -> ApplicantDataset:
    """runs the full Phase 0-2 pipeline once — see module docstring for why this is a batch preload, not per-request."""
    from src.features.cross_source import compute_batch_cross_source, merge_into_vectors
    from src.features.engine import FeatureEngine
    from src.ingestion.applicant_adapter import load_and_adapt, to_engine_frames

    result = load_and_adapt()
    bureau_df, bank_df, itr_df = to_engine_frames(result)
    engine = FeatureEngine()
    vectors = engine.compute_batch(bureau_df, bank_df, itr_df)
    cross_source_by_id = compute_batch_cross_source(result)
    vectors = merge_into_vectors(vectors, cross_source_by_id)

    return ApplicantDataset(
        master_by_id={m.applicant_id: m.model_dump() for m in result.master},
        bureau_by_id={b.applicant_id: b.model_dump() for b in result.bureau},
        bank_by_id={b.applicant_id: b.model_dump() for b in result.bank},
        vector_by_id={v.applicant_id: v.model_dump() for v in vectors},
    )
