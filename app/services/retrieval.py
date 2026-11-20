"""Tenant-safe retrieval.

company_id and version are passed in by trusted server code (JWT + SQLite). This module
never reads them from a request. It also re-checks every row Chroma returns and fails
closed if one belongs to another company or version.
"""
from dataclasses import dataclass
from typing import Optional

from app.db.chroma import ChunkStore


class TenantViolation(Exception):
    """Chroma returned a chunk outside the caller's company/version. Should be impossible."""


@dataclass(frozen=True)
class Evidence:
    chunk_id: str
    company_id: str
    policy_version: str
    document: str
    section: str
    text: str
    distance: float


@dataclass
class Retrieval:
    evidence: list  # at most settings.max_evidence items, best first
    best_distance: Optional[float]  # best candidate distance BEFORE thresholding (for tuning)


class Retriever:
    def __init__(self, store: ChunkStore, settings):
        self.store = store
        self.s = settings

    def retrieve(self, company_id: str, version: str, question: str) -> Retrieval:
        rows = self.store.query(company_id, version, question, self.s.candidate_pool)
        found = []
        for r in rows:
            m = r["metadata"]
            if m.get("company_id") != company_id or m.get("policy_version") != version:
                raise TenantViolation(f"chunk {r['id']} is outside {company_id}/{version}")
            found.append(
                Evidence(r["id"], m["company_id"], m["policy_version"], m["document_name"],
                         m["section"], r["text"], r["distance"])
            )
        found.sort(key=lambda e: e.distance)
        best = found[0].distance if found else None
        relevant = [e for e in found if e.distance <= self.s.distance_limit]
        return Retrieval(relevant[: self.s.max_evidence], best)
