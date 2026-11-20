import pytest

from app.services.ingestion import IngestionFailed, InvalidPolicy, VersionExists, ingest_policy, reconcile_staging
from app.services.seed import seed
from tests.conftest import read_policy


def test_initial_ingest_is_active_and_searchable(container):
    v = container.db.get_active_version("acme")
    assert v["version"] == "v1" and v["status"] == "active" and v["chunk_count"] == 9
    assert container.store.count("acme", "v1") == 9
    row = container.store.query("acme", "v1", "weekend saturday", 1)[0]
    m = row["metadata"]
    for key in ("company_id", "policy_id", "policy_version", "document_name", "section",
                "effective_date", "content_hash", "chunk_index"):
        assert key in m
    assert m["effective_date"] == "2026-01-01" and m["document_name"] == "acme_transport_policy_v1.md"


def test_seed_is_idempotent(container):
    before = (container.store.count("acme", "v1"), len(container.db.list_versions("acme")))
    seed(container)
    seed(container)
    assert (container.store.count("acme", "v1"), len(container.db.list_versions("acme"))) == before


def test_new_version_replaces_old_and_only_new_answers(container, client_for, headers):
    client = client_for(container)
    h = headers["acme_admin"]
    r = client.post("/admin/policies", headers=h, json={
        "version": "v2", "document_name": "acme_transport_policy_v2.md",
        "content": read_policy("acme_transport_policy_v2.md")})
    assert r.status_code == 201 and r.json()["status"] == "active"

    versions = {v["version"]: v["status"] for v in container.db.list_versions("acme")}
    assert versions == {"v1": "inactive", "v2": "active"}

    # old chunks still exist in Chroma (audit) but can never be retrieved
    assert container.store.count("acme", "v1") == 9
    r = client.post("/questions", json={"question": "At what time is a security escort needed on late drops?"},
                    headers=headers["acme_employee"])
    body = r.json()
    assert body["policy_version"] == "v2"
    assert body["meta"]["retrieved_chunk_ids"] and all("-v2-" in c for c in body["meta"]["retrieved_chunk_ids"])
    ev_text = " ".join(e.text for _, ev in container.llm.calls for e in ev)
    assert "9 PM" in ev_text and "after 8 PM must be accompanied" not in ev_text


def test_other_companies_unaffected_by_a_new_version(container):
    ingest_policy(container.db, container.store, container.settings, "acme", "v2", "a2.md",
                  read_policy("acme_transport_policy_v2.md"))
    assert container.db.get_active_version("orbit")["version"] == "v1"
    assert container.db.get_active_version("northstar")["version"] == "v1"


def test_duplicate_version_is_rejected(container, client_for, headers):
    r = client_for(container).post("/admin/policies", headers=headers["acme_admin"], json={
        "version": "v1", "document_name": "x.md", "content": read_policy("acme_transport_policy_v1.md")})
    assert r.status_code == 409
    assert container.store.count("acme", "v1") == 9  # nothing duplicated


@pytest.mark.parametrize("text", ["", "   \n  ", "no headings at all, just text", "## Empty section\n\n## Another\n"])
def test_empty_or_unparseable_policy_rejected_and_old_version_kept(container, text):
    with pytest.raises(InvalidPolicy):
        ingest_policy(container.db, container.store, container.settings, "acme", "v2", "bad.md", text)
    assert container.db.get_active_version("acme")["version"] == "v1"
    assert [v["version"] for v in container.db.list_versions("acme")] == ["v1"]  # nothing even staged


def test_invalid_version_label_and_unknown_company(container):
    with pytest.raises(InvalidPolicy):
        ingest_policy(container.db, container.store, container.settings, "acme", "v 2/../", "a.md", "## A\nb")
    with pytest.raises(InvalidPolicy):
        ingest_policy(container.db, container.store, container.settings, "nobody", "v1", "a.md", "## A\nb")


def test_index_failure_midway_keeps_old_version_and_cleans_partial_chunks(container, client_for, headers, monkeypatch):
    real_add = container.store.add

    def half_then_boom(ids, documents, metadatas):
        real_add(ids[:2], documents[:2], metadatas[:2])  # a partial write really lands in Chroma
        raise RuntimeError("embedding service exploded")

    monkeypatch.setattr(container.store, "add", half_then_boom)
    with pytest.raises(IngestionFailed):
        ingest_policy(container.db, container.store, container.settings, "acme", "v2", "a2.md",
                      read_policy("acme_transport_policy_v2.md"))

    assert container.db.get_active_version("acme")["version"] == "v1"      # pointer untouched
    assert container.store.count("acme", "v2") == 0                        # partial chunks removed
    states = {v["version"]: v["status"] for v in container.db.list_versions("acme")}
    assert states == {"v1": "active", "v2": "failed"}

    # users still get the v1 answer
    r = client_for(container).post("/questions", json={"question": "Can I book a cab on Saturday?"},
                                   headers=headers["acme_employee"])
    assert r.json()["policy_version"] == "v1"


def test_failed_version_can_be_retried(container, monkeypatch):
    monkeypatch.setattr(container.store, "add", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(IngestionFailed):
        ingest_policy(container.db, container.store, container.settings, "acme", "v2", "a2.md",
                      read_policy("acme_transport_policy_v2.md"))
    monkeypatch.undo()
    result = ingest_policy(container.db, container.store, container.settings, "acme", "v2", "a2.md",
                           read_policy("acme_transport_policy_v2.md"))
    assert result["status"] == "active" and container.db.get_active_version("acme")["version"] == "v2"


def test_failure_at_activation_step_keeps_old_version(container, monkeypatch):
    monkeypatch.setattr(container.db, "activate", lambda *a: (_ for _ in ()).throw(RuntimeError("db locked")))
    with pytest.raises(IngestionFailed):
        ingest_policy(container.db, container.store, container.settings, "acme", "v2", "a2.md",
                      read_policy("acme_transport_policy_v2.md"))
    assert container.db.get_active_version("acme")["version"] == "v1"
    assert container.store.count("acme", "v2") == 0


def test_reconcile_cleans_up_interrupted_ingestion(container):
    vid = container.db.create_staged("acme", "v9", "crashed.md", None)  # simulate a crash: staging row left behind
    container.store.add(["acme-v9-00-x"], ["orphan"], [{"company_id": "acme", "policy_version": "v9",
                                                        "document_name": "d", "section": "s"}])
    assert reconcile_staging(container.db, container.store) == 1
    assert container.store.count("acme", "v9") == 0
    assert {v["version"]: v["status"] for v in container.db.list_versions("acme")}["v9"] == "failed"
    assert vid


def test_only_one_active_version_is_enforced_by_the_database(container):
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        with container.db._conn() as c:
            c.execute("INSERT INTO policy_versions(company_id, version, document_name, status, created_at) "
                      "VALUES ('acme', 'vX', 'x', 'active', 'now')")


def test_admin_cannot_ingest_for_another_company(client, headers, container):
    r = client.post("/admin/policies", headers=headers["acme_admin"], json={
        "version": "v2", "document_name": "x.md", "content": "## A\nb", "company_id": "orbit"})
    assert r.status_code == 422  # no such field
    r = client.post("/admin/policies", headers=headers["acme_employee"], json={
        "version": "v2", "document_name": "x.md", "content": "## A\nb"})
    assert r.status_code == 403
    assert container.db.get_active_version("orbit")["version"] == "v1"
