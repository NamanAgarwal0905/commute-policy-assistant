import re

from app.services.ingestion import chunk_policy, parse_sections
from tests.conftest import read_policy


def test_sections_are_parsed_with_titles():
    sections = parse_sections(read_policy("acme_transport_policy_v1.md"))
    titles = [t for t, _ in sections]
    assert titles[0] == "1. Eligibility" and "6. Weekend and Holiday Travel" in titles and len(titles) == 9


def test_section_mode_one_chunk_per_section_with_heading_in_text():
    chunks = chunk_policy(read_policy("acme_transport_policy_v1.md"), "section", 500, 100, 1200)
    assert len(chunks) == 9
    for title, text in chunks:
        assert text.startswith(title)


def test_long_section_is_split_but_keeps_its_section_title():
    long_text = "## 1. Big\n" + ("Rule sentence number one. " * 200)
    chunks = chunk_policy(long_text, "section", 500, 100, 1200)
    assert len(chunks) > 1 and all(t == "1. Big" for t, _ in chunks)
    assert all(len(c) <= 1200 + len("1. Big\n") for _, c in chunks)


def test_fixed_mode_respects_size_and_labels_sections_by_position():
    chunks = chunk_policy(read_policy("acme_transport_policy_v1.md"), "fixed", 500, 100, 1200)
    assert all(len(c) <= 500 for _, c in chunks)
    assert chunks[0][0] == "Document Header"  # first window starts before any '## ' heading


def test_chunk_ids_are_unique_and_metadata_complete(container):
    got = container.store.col.get(where={"company_id": "orbit"}, include=["metadatas"])
    assert len(got["ids"]) == len(set(got["ids"])) == 8
    assert all(re.match(r"^orbit-v1-\d\d-", i) for i in got["ids"])
    assert {m["section"] for m in got["metadatas"]} >= {"2. Night Shift and Late Drops", "6. Weekend and Holiday Travel"}


def test_best_chunk_is_the_right_section(container):
    ev = container.retriever.retrieve("northstar", "v1", "Can I book a Saturday cab?").evidence
    assert ev[0].section == "6. Weekend and Holiday Travel" and ev[0].document == "northstar_transport_policy_v1.md"


def test_evidence_is_capped_at_three_and_sorted(container):
    ev = container.retriever.retrieve("acme", "v1", "escort saturday airport approval manager hours booking").evidence
    assert 0 < len(ev) <= 3
    assert [e.distance for e in ev] == sorted(e.distance for e in ev)


def test_irrelevant_question_returns_no_evidence(container):
    res = container.retriever.retrieve("acme", "v1", "quantum chromodynamics banana smoothie")
    assert res.evidence == [] and res.best_distance is not None


def test_stricter_threshold_returns_less(make_container):
    strict = make_container(max_distance=0.01)
    assert strict.retriever.retrieve("acme", "v1", "Can I book a cab on Saturday?").evidence == []


def test_citation_metadata_is_preserved_on_evidence(container):
    e = container.retriever.retrieve("orbit", "v1", "escort after what time").evidence[0]
    assert (e.company_id, e.policy_version) == ("orbit", "v1")
    assert e.chunk_id.startswith("orbit-v1-") and e.section and e.document
