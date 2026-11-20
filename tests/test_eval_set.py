"""The evaluation set must be true to the policy text, and the runner must be repeatable."""
import json
import os

from app.services.ingestion import parse_sections
from scripts.evaluate import QUESTIONS, fact_present, norm, run_evaluation
from tests.conftest import read_policy

CASES = [json.loads(line) for line in open(QUESTIONS, encoding="utf-8") if line.strip()]
FILES = {"acme": "acme_transport_policy_v1.md", "northstar": "northstar_transport_policy_v1.md",
         "orbit": "orbit_transport_policy_v1.md"}


def sections(company):
    return dict(parse_sections(read_policy(FILES[company])))


def test_format_and_size():
    assert len(CASES) >= 10
    assert len({c["id"] for c in CASES}) == len(CASES)
    for c in CASES:
        assert set(c) == {"id", "input", "expected", "actual", "pass", "notes"}
        assert c["actual"] is None and c["pass"] is None  # the committed set is never pre-filled
        assert c["input"]["company_id"] in FILES and c["input"]["question"]


def test_mix_of_case_types():
    ids = [c["id"] for c in CASES]
    assert any(i.startswith("P") for i in ids)       # paraphrases
    assert any(i.startswith(("X", "SEC")) for i in ids)  # cross-company / adversarial
    assert sum(c["expected"]["status"] == "not_covered" for c in CASES) >= 3


def test_gold_facts_and_sections_are_verified_against_the_policy_text():
    for c in CASES:
        exp, secs = c["expected"], sections(c["input"]["company_id"])
        for wanted in exp.get("required_citations", []):
            matches = [t for t in secs if norm(wanted) in norm(t)]
            assert matches, f"{c['id']}: section {wanted!r} not found in policy"
            for fact in exp.get("required_facts", []):
                assert any(fact_present(fact, secs[t]) for t in matches), f"{c['id']}: fact {fact!r} not in section"


def test_forbidden_values_are_not_in_the_askers_own_policy():
    for c in CASES:
        own = norm(read_policy(FILES[c["input"]["company_id"]]))
        for bad in c["expected"].get("forbidden_text", []):
            if bad in ("Northstar", "Orbit"):
                continue
            assert norm(bad) not in own, f"{c['id']}: forbidden {bad!r} appears in own policy"


def test_not_covered_topics_really_absent_from_policy():
    for c in CASES:
        if c["expected"]["status"] == "not_covered":
            text = norm(read_policy(FILES[c["input"]["company_id"]]))
            for word in ("pet", "errand", "airport", "electric"):
                if word in norm(c["input"]["question"]) and c["id"] != "O03" or (c["id"] == "O03" and word == "airport"):
                    assert word not in text.replace("petty", ""), f"{c['id']}: {word} unexpectedly in policy"


def test_runner_is_repeatable_and_leak_free():
    kw = dict(embedding_provider="hash", llm_provider="extractive")
    rows1, m1 = run_evaluation("section", **kw)
    rows2, m2 = run_evaluation("section", **kw)
    assert [r["pass"] for r in rows1] == [r["pass"] for r in rows2]
    assert [r["actual"]["status"] for r in rows1] == [r["actual"]["status"] for r in rows2]
    for m in (m1, m2):
        assert m["tenant_leakage"] == 0 and m["version_leakage"] == 0
        assert m["llm_calls_max"] <= 2 and m["citation_validity"] == 1.0
    assert all(set(r) == {"id", "input", "expected", "actual", "pass", "notes"} for r in rows1)
    assert not os.path.exists("evaluation/results_test_tmp.jsonl")


def test_runner_with_fake_llm_that_always_abstains():
    from tests.conftest import FakeLLM
    rows, m = run_evaluation("section", llm=FakeLLM(lambda q, ev: json.dumps({"answerable": False})),
                             embedding_provider="hash", llm_provider="extractive")
    assert m["abstention_recall"] == 1.0 and m["tenant_leakage"] == 0
