"""Run the fixed evaluation set and report metrics.

    python -m scripts.evaluate                      # run once, write evaluation/results.jsonl
    python -m scripts.evaluate --repeat 5           # repeat each question 5x for more latency samples
    python -m scripts.evaluate --compare            # bonus: fixed-size vs section-aware chunking
    python -m scripts.evaluate --show-distances     # help choose MAX_DISTANCE for your embedding model

Each run builds a FRESH temporary database + Chroma index from data/policies, so it never touches
live data and is repeatable. It goes through the real HTTP stack (JWT login, /questions) using
FastAPI's in-process TestClient. LLM and embedding model come from the same env vars as the app.
"""
import argparse
import json
import logging
import math
import os
import re
import secrets
import tempfile

os.environ.setdefault("JWT_SECRET", secrets.token_hex(32))  # throwaway secret for this temp environment

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.core.container import Container, get_container  # noqa: E402
from app.main import app  # noqa: E402
from app.services.seed import DEMO_PASSWORD, seed  # noqa: E402

logging.getLogger().setLevel(logging.ERROR)  # keep the report readable; failures are printed below
logging.getLogger("app.services.answer").setLevel(logging.WARNING)
for _name in ("httpx", "httpx2", "httpcore"):
    logging.getLogger(_name).setLevel(logging.ERROR)

QUESTIONS = "evaluation/questions.jsonl"


# ------------------------------------------------------------------ judging
def norm(s: str) -> str:
    """Normalise text so '24-hour', '24 hours', 'INR 1,500', '10:00 PM' compare fairly."""
    s = s.lower().replace("’", "'").replace("p.m.", "pm").replace("a.m.", "am")
    s = re.sub(r"(?<=\d),(?=\d)", "", s)
    s = re.sub(r"[-–‑]", " ", s)
    s = re.sub(r"(\d+):00", r"\1", s)
    s = re.sub(r"(\d)\s*(am|pm)\b", r"\1 \2", s)
    s = re.sub(r"(\d)\s*km\b", r"\1 km", s)
    return re.sub(r"\s+", " ", s).strip()


def fact_present(fact, text: str) -> bool:
    """A fact is a string, or a list of acceptable alternatives (any one is enough)."""
    options = fact if isinstance(fact, list) else [fact]
    return any(norm(o) in norm(text) for o in options)


def percentile(values, p):
    if not values:
        return None
    v = sorted(values)
    return v[max(0, math.ceil(p / 100 * len(v)) - 1)]


def judge(case: dict, resp: dict, container) -> tuple:
    """Return (passed, notes, details). Rules are explicit and identical for every run."""
    exp, company = case["expected"], case["input"]["company_id"]
    store = container.store
    notes, details = [], {}

    allowed = exp["status"] if isinstance(exp["status"], list) else [exp["status"]]
    status, answer = resp["status"], resp["answer"]
    if status not in allowed:
        notes.append(f"status {status!r} not in expected {allowed}")

    if status == "answered":
        for fact in exp.get("required_facts", []):
            if not fact_present(fact, answer):
                notes.append(f"missing fact {fact!r}")
        wanted = exp.get("required_citations", [])
        cited = [norm(c["section"]) for c in resp["citations"]]
        if wanted and not any(norm(w) in c for w in wanted for c in cited):
            notes.append(f"citation sections {cited} do not include any of {wanted}")
        if not resp["citations"]:
            notes.append("answered without a citation")
    elif status == "not_covered" and "transport desk" not in answer.lower():
        notes.append("not-covered message does not point to the transport desk")

    for bad in exp.get("forbidden_text", []):
        if norm(bad) in norm(answer):
            notes.append(f"answer contains forbidden text {bad!r}")

    # structural isolation checks on every chunk that reached the LLM
    active = container.db.get_active_version(company)
    tenant_leak = version_leak = 0
    retrieved_sections = []
    for cid in resp["meta"]["retrieved_chunk_ids"]:
        row = store.get(cid)
        m = row["metadata"] if row else {}
        retrieved_sections.append(norm(m.get("section", "")))
        if m.get("company_id") != company:
            tenant_leak += 1
            notes.append(f"TENANT LEAK: chunk {cid}")
        if active and m.get("policy_version") != active["version"]:
            version_leak += 1
            notes.append(f"VERSION LEAK: chunk {cid}")

    # citation validity: stored document + section + company must match what was returned
    valid_cites = 0
    for c in resp["citations"]:
        row = store.get(c["chunk_id"])
        m = row["metadata"] if row else {}
        if (m.get("document_name") == c["document"] and m.get("section") == c["section"]
                and m.get("company_id") == company):
            valid_cites += 1
        else:
            notes.append(f"invalid citation {c['chunk_id']}")
    if len(resp["citations"]) > 3:
        notes.append("more than 3 citations")
    if resp["meta"]["llm_calls"] > 2:
        notes.append("more than 2 LLM calls")

    wanted = exp.get("required_citations", [])
    details.update(
        tenant_leak=tenant_leak, version_leak=version_leak,
        citations=len(resp["citations"]), valid_citations=valid_cites,
        hit3=(any(norm(w) in s for w in wanted for s in retrieved_sections)
              if wanted and exp["status"] == "answered" else None),
    )
    return (not notes), notes, details


# ------------------------------------------------------------------ runner
def run_evaluation(chunk_mode="section", repeat=1, questions_path=QUESTIONS, llm=None, **overrides):
    """Build a fresh temp environment, run every case, return (rows, metrics)."""
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(data_dir=tmp, chunk_mode=chunk_mode, **overrides)
        container = Container(settings, llm=llm)
        seed(container)
        app.dependency_overrides[get_container] = lambda: container
        try:
            client = TestClient(app)
            tokens = {}
            for co in ("acme", "northstar", "orbit"):
                r = client.post("/auth/token", json={"username": f"{co}_employee", "password": DEMO_PASSWORD})
                tokens[co] = {"Authorization": "Bearer " + r.json()["access_token"]}

            cases = [json.loads(line) for line in open(questions_path, encoding="utf-8") if line.strip()]
            rows, latencies, llm_calls = [], [], []
            agg = dict(tenant=0, version=0, cites=0, valid=0, answered=0, answered_with_cite=0,
                       hit_total=0, hit_ok=0, tp=0, fp=0, fn=0)
            import time

            for case in cases:
                co, q = case["input"]["company_id"], case["input"]["question"]
                resp, http_status = None, None
                for _ in range(repeat):
                    t0 = time.perf_counter()
                    r = client.post("/questions", json={"question": q}, headers=tokens[co])
                    latencies.append((time.perf_counter() - t0) * 1000)
                    resp, http_status = r.json(), r.status_code
                    llm_calls.append(resp["meta"]["llm_calls"])
                    time.sleep(4)
                passed, notes, d = judge(case, resp, container)

                agg["tenant"] += d["tenant_leak"]; agg["version"] += d["version_leak"]
                agg["cites"] += d["citations"]; agg["valid"] += d["valid_citations"]
                if resp["status"] == "answered":
                    agg["answered"] += 1
                    agg["answered_with_cite"] += 1 if d["valid_citations"] > 0 else 0
                if d["hit3"] is not None:
                    agg["hit_total"] += 1; agg["hit_ok"] += 1 if d["hit3"] else 0
                exp_status = case["expected"]["status"]
                if resp["status"] == "not_covered":
                    if exp_status == "not_covered": agg["tp"] += 1
                    elif exp_status == "answered": agg["fp"] += 1
                if exp_status == "not_covered" and resp["status"] != "not_covered":
                    agg["fn"] += 1

                rows.append({
                    "id": case["id"], "input": case["input"], "expected": case["expected"],
                    "actual": {"http_status": http_status, "status": resp["status"], "answer": resp["answer"],
                               "citations": resp["citations"], "policy_version": resp["policy_version"],
                               "retrieved_chunk_ids": resp["meta"]["retrieved_chunk_ids"],
                               "best_distance": resp["meta"]["best_distance"],
                               "llm_calls": resp["meta"]["llm_calls"], "latency_ms": resp["meta"]["latency_ms"]},
                    "pass": passed, "notes": "; ".join(notes) if notes else case.get("notes", ""),
                })

            def ratio(a, b):
                return round(a / b, 3) if b else None

            chunk_count = sum(v["chunk_count"] for co in ("acme", "northstar", "orbit")
                              for v in container.db.list_versions(co))
            metrics = {
                "chunk_mode": chunk_mode, "embedding": settings.embedding_provider, "llm": settings.llm_provider,
                "cases": len(rows), "passed": sum(r["pass"] for r in rows),
                "answer_correctness": ratio(sum(r["pass"] for r in rows), len(rows)),
                "retrieval_hit_at_3": ratio(agg["hit_ok"], agg["hit_total"]),
                "citation_validity": ratio(agg["valid"], agg["cites"]),
                "citation_coverage": ratio(agg["answered_with_cite"], agg["answered"]),
                "abstention_precision": ratio(agg["tp"], agg["tp"] + agg["fp"]),
                "abstention_recall": ratio(agg["tp"], agg["tp"] + agg["fn"]),
                "tenant_leakage": agg["tenant"], "version_leakage": agg["version"],
                "latency_samples": len(latencies),
                "latency_p50_ms": round(percentile(latencies, 50), 1),
                "latency_p95_ms": round(percentile(latencies, 95), 1),
                "llm_calls_avg": round(sum(llm_calls) / len(llm_calls), 2), "llm_calls_max": max(llm_calls),
                "index_chunks": chunk_count,
            }
            return rows, metrics
        finally:
            app.dependency_overrides.pop(get_container, None)


# ------------------------------------------------------------------ CLI
def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def print_metrics(m):
    print(f"\n== chunk_mode={m['chunk_mode']}  embedding={m['embedding']}  llm={m['llm']} ==")
    for k, v in m.items():
        if k not in ("chunk_mode", "embedding", "llm"):
            print(f"  {k:22} {v}")


def print_failures(rows):
    for r in rows:
        if not r["pass"]:
            print(f"  FAIL {r['id']}: {r['notes']}")


def show_distances(rows):
    ans, nc = [], []
    print("\nBest retrieval distance per case (lower = more similar):")
    for r in rows:
        exp = r["expected"]["status"]
        d = r["actual"]["best_distance"]
        print(f"  {r['id']:6} expected={str(exp):28} best_distance={d if d is None else round(d, 3)}")
        if d is not None and exp == "answered":
            ans.append(d)
        if d is not None and exp == "not_covered":
            nc.append(d)
    if ans and nc:
        hi, lo = max(ans), min(nc)
        if hi < lo:
            print(f"\nSeparable. Suggested MAX_DISTANCE ~ {round((hi + lo) / 2, 2)} (answered max {hi:.3f} < not-covered min {lo:.3f})")
        else:
            print(f"\nNOT separable by distance alone (answered max {hi:.3f} >= not-covered min {lo:.3f}). "
                  "Keep MAX_DISTANCE lenient and rely on the LLM to abstain.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunk-mode", choices=["section", "fixed"], default="section")
    ap.add_argument("--repeat", type=int, default=1, help="ask each question N times (more latency samples)")
    ap.add_argument("--out", default="evaluation/results.jsonl")
    ap.add_argument("--compare", action="store_true", help="fixed-size (baseline) vs section-aware chunking")
    ap.add_argument("--show-distances", action="store_true")
    args = ap.parse_args()

    if args.compare:
        out = {}
        for mode in ("fixed", "section"):
            rows, m = run_evaluation(mode, args.repeat)
            write_jsonl(f"evaluation/results_{mode}.jsonl", rows)
            print_metrics(m); print_failures(rows)
            out[mode] = m
        with open("evaluation/comparison.json", "w") as f:
            json.dump(out, f, indent=2)
        print("\nwrote evaluation/results_fixed.jsonl, results_section.jsonl, comparison.json")
    else:
        rows, m = run_evaluation(args.chunk_mode, args.repeat)
        write_jsonl(args.out, rows)
        print_metrics(m); print_failures(rows)
        with open("evaluation/metrics.json", "w") as f:
            json.dump(m, f, indent=2)
        print(f"\nwrote {args.out} and evaluation/metrics.json")
        if args.show_distances:
            show_distances(rows)
