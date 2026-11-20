"""Chroma adapter: stores policy chunks + embeddings and runs similarity search.

SECURITY NOTE: the tenant filter is built in exactly one place (_where). query() and
delete_version() REQUIRE company_id and version arguments, so there is no code path that
searches "everything". Chroma is a search index, not an authorization system: the caller
(retrieval.py) still re-checks every returned row.
"""
import chromadb

COLLECTION = "policy_chunks"


def _where(company_id: str, version: str) -> dict:
    return {"$and": [{"company_id": {"$eq": company_id}}, {"policy_version": {"$eq": version}}]}


class ChunkStore:
    def __init__(self, path: str, embedding_function):
        self.client = chromadb.PersistentClient(path=path)
        self.col = self.client.get_or_create_collection(
            COLLECTION, embedding_function=embedding_function, metadata={"hnsw:space": "cosine"}
        )

    def add(self, ids: list, documents: list, metadatas: list) -> None:
        self.col.add(ids=ids, documents=documents, metadatas=metadatas)

    def count(self, company_id: str, version: str) -> int:
        return len(self.col.get(where=_where(company_id, version), include=[])["ids"])

    def delete_version(self, company_id: str, version: str) -> None:
        self.col.delete(where=_where(company_id, version))

    def get(self, chunk_id: str):
        """Fetch one chunk's stored metadata (used by the evaluator to verify citations)."""
        res = self.col.get(ids=[chunk_id], include=["metadatas", "documents"])
        if not res["ids"]:
            return None
        return {"id": chunk_id, "text": res["documents"][0], "metadata": res["metadatas"][0]}

    def query(self, company_id: str, version: str, text: str, n: int) -> list:
        res = self.col.query(
            query_texts=[text],
            n_results=n,
            where=_where(company_id, version),
            include=["documents", "metadatas", "distances"],
        )
        rows = []
        for i, cid in enumerate(res["ids"][0]):
            rows.append(
                {
                    "id": cid,
                    "text": res["documents"][0][i],
                    "metadata": res["metadatas"][0][i],
                    "distance": res["distances"][0][i],
                }
            )
        return rows
