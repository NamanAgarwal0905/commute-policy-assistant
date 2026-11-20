"""The question flow. Everything that matters for safety is plain code in this file:

  active version (SQLite) -> filtered retrieval -> threshold -> ONE LLM call
  -> validate output -> build citations from stored metadata -> response

LLM calls per question: 0 when nothing relevant is found, otherwise exactly 1, never more
than MAX_LLM_CALLS (guarded in _call_llm). Cited chunks: at most settings.max_evidence (3).
There are no retries, so a failure costs one call and returns "try again" quickly.
"""
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from app.services.llm import LLMError, LLMOutputError, parse_output
from app.services.retrieval import TenantViolation

log = logging.getLogger(__name__)

MAX_LLM_CALLS = 2
NOT_COVERED_MSG = "The current transport policy does not say anything about this. Please contact your transport desk."
RETRY_MSG = "I couldn't check the policy right now. Please try again in a moment."

_pool = ThreadPoolExecutor(max_workers=8)


def run_with_timeout(fn, seconds: float):
    """Run fn in a worker thread and give up after `seconds` (raises TimeoutError)."""
    future = _pool.submit(fn)
    try:
        return future.result(timeout=seconds)
    finally:
        future.cancel()  # no-op if already running; a stuck worker is abandoned, not awaited


class AnswerService:
    def __init__(self, db, retriever, llm, settings):
        self.db, self.retriever, self.llm, self.s = db, retriever, llm, settings

    def answer(self, company_id: str, question: str) -> dict:
        """company_id MUST come from a verified token. It is never taken from the question."""
        start = time.monotonic()
        meta = {"llm_calls": 0, "retrieved_chunk_ids": [], "best_distance": None}

        def respond(status, message, citations=(), version=None):
            meta["latency_ms"] = int((time.monotonic() - start) * 1000)
            return {
                "status": status,
                "answer": message,
                "citations": list(citations),
                "policy_version": version,
                "request_id": "req_" + uuid.uuid4().hex[:12],
                "meta": meta,
            }

        try:
            active = self.db.get_active_version(company_id)
            if active is None:
                return respond("not_covered", NOT_COVERED_MSG)
            version = active["version"]

            retrieval = run_with_timeout(
                lambda: self.retriever.retrieve(company_id, version, question), self.s.retrieval_timeout_seconds
            )
            evidence = retrieval.evidence
            meta["retrieved_chunk_ids"] = [e.chunk_id for e in evidence]
            meta["best_distance"] = retrieval.best_distance
            if not evidence:  # nothing relevant: say so without spending an LLM call
                return respond("not_covered", NOT_COVERED_MSG, version=version)

            raw = self._call_llm(question, evidence, meta)
            parsed = parse_output(raw, {e.chunk_id for e in evidence})
            if not parsed.answerable:
                return respond("not_covered", NOT_COVERED_MSG, version=version)

            # Citations come from OUR stored metadata for validated chunk ids, never from model text.
            by_id = {e.chunk_id: e for e in evidence}
            citations = [
                {"document": by_id[i].document, "section": by_id[i].section, "chunk_id": i}
                for i in parsed.supporting_chunk_ids
            ]
            return respond("answered", parsed.answer, citations, version)

        except TenantViolation:
            log.error("TENANT VIOLATION for company %s - failing closed", company_id)
        except TimeoutError:
            log.warning("timeout while answering for company %s", company_id)
        except (LLMError, LLMOutputError) as exc:
            log.warning("LLM problem for company %s: %s", company_id, exc)
        except Exception:
            log.exception("unexpected error while answering for company %s", company_id)
        return respond("retry", RETRY_MSG)  # never a partial answer

    def _call_llm(self, question, evidence, meta):
        if meta["llm_calls"] >= MAX_LLM_CALLS:  # safety net for future edits
            raise LLMError("LLM call budget exhausted")
        meta["llm_calls"] += 1
        # Hard wall-clock bound on top of the HTTP client's own timeout.
        return run_with_timeout(lambda: self.llm.generate(question, evidence), self.s.llm_timeout_seconds + 0.5)
