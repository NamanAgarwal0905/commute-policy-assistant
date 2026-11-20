"""Policy ingestion: parse -> chunk -> index -> verify -> activate.

Staged activation: the old version keeps answering until the new one is fully indexed
and verified. Only then does ONE SQLite transaction flip the active version.
If anything fails before that, the new version is marked 'failed', its partial chunks
are deleted, and the old active version is untouched.

Limitation: SQLite and Chroma have no shared transaction. A crash between "chunks written"
and "activated" leaves orphan vectors; reconcile_staging() cleans these up at startup.
They are never served anyway, because retrieval filters on the active version.
"""
import hashlib
import logging
import re
from typing import Optional

from app.db.metadata import MetadataDB, VersionExists  # noqa: F401  (VersionExists re-exported)
from app.db.chroma import ChunkStore

log = logging.getLogger(__name__)

MAX_POLICY_CHARS = 200_000
VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,20}$")
HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)
POLICY_ID = "transport-policy"


class InvalidPolicy(Exception):
    """The submitted policy is unusable (client error)."""


class IngestionFailed(Exception):
    """Indexing failed (server error). The previous version is still active."""


# ---------------------------------------------------------------- parsing / chunking
def _clean(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text.replace("\r\n", "\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_sections(text: str) -> list:
    """Split a Markdown policy on '## ' headings -> [(title, body), ...]. Empty sections are dropped."""
    matches = list(HEADING_RE.finditer(text))
    sections = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = _clean(text[m.end():end])
        if body:
            sections.append((m.group(1).strip(), body))
    return sections


def _windows(text: str, size: int, overlap: int) -> list:
    """Cut text into windows of ~size chars with overlap -> [(start_offset, window_text)]."""
    out, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):  # avoid cutting mid-word where possible
            space = text.rfind(" ", start + size // 2, end)
            end = space if space > 0 else end
        out.append((start, text[start:end].strip()))
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return out


def _slug(title: str) -> str:
    title = re.sub(r"^\d+[.)]\s*", "", title)
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "section"


def chunk_policy(text: str, mode: str, size: int, overlap: int, max_section: int) -> list:
    """Return [(section_title, chunk_text), ...].

    mode="section" (default): one chunk per policy section, heading kept in the text.
                              Only a section longer than max_section is split further.
    mode="fixed" (baseline for the bonus experiment): ignore structure, cut the whole
                              document into fixed windows. Section = last heading before the window.
    """
    if mode == "section":
        chunks = []
        for title, body in parse_sections(text):
            if len(body) <= max_section:
                chunks.append((title, f"{title}\n{body}"))
            else:
                for _, w in _windows(body, max_section, overlap):
                    chunks.append((title, f"{title}\n{w}"))
        return chunks

    flat = _clean(text)
    headings = [(m.start(), m.group(1).strip()) for m in HEADING_RE.finditer(flat)]
    chunks = []
    for start, w in _windows(flat, size, overlap):
        title = "Document Header"
        for pos, h in headings:
            if pos <= start:
                title = h
        chunks.append((title, w))
    return chunks


def _effective_date(text: str) -> Optional[str]:
    m = re.search(r"^Effective date:\s*(\d{4}-\d{2}-\d{2})", text, re.M)
    return m.group(1) if m else None


# ---------------------------------------------------------------- ingestion
def ingest_policy(db: MetadataDB, store: ChunkStore, settings, company_id: str, version: str,
                  document_name: str, text: str) -> dict:
    # 1. validate input (no state touched yet)
    if not db.company_exists(company_id):
        raise InvalidPolicy("unknown company")
    if not VERSION_RE.match(version):
        raise InvalidPolicy("version must be 1-20 chars: letters, digits, '.', '_' or '-'")
    if not text or not text.strip():
        raise InvalidPolicy("policy text is empty")
    if len(text) > MAX_POLICY_CHARS:
        raise InvalidPolicy("policy text too large")
    if not parse_sections(text):
        raise InvalidPolicy("no '## ' sections with content found in the policy")

    # 2. register as staging (raises VersionExists if this label is already used)
    effective = _effective_date(text)
    version_id = db.create_staged(company_id, version, document_name, effective)

    # 3. index + verify; anything going wrong rolls the staging version back
    try:
        pieces = chunk_policy(text, settings.chunk_mode, settings.chunk_size_chars,
                              settings.chunk_overlap_chars, settings.max_section_chars)
        pieces = [(t, c) for t, c in pieces if c.strip()]
        if not pieces:
            raise InvalidPolicy("policy produced no chunks")
        ids, docs, metas = [], [], []
        for i, (section, chunk_text) in enumerate(pieces):
            ids.append(f"{company_id}-{version}-{i:02d}-{_slug(section)}")
            docs.append(chunk_text)
            metas.append({
                "company_id": company_id,
                "policy_id": POLICY_ID,
                "policy_version": version,
                "document_name": document_name,
                "section": section,
                "effective_date": effective or "",
                "chunk_index": i,
                "content_hash": "sha256:" + hashlib.sha256(chunk_text.encode()).hexdigest(),
            })
        store.add(ids, docs, metas)

        # verify: all chunks present, and the staged version is actually searchable
        if store.count(company_id, version) != len(ids):
            raise IngestionFailed("chunk count mismatch after indexing")
        probe = store.query(company_id, version, docs[0], 1)
        if not probe:
            raise IngestionFailed("staged version is not retrievable")

        db.activate(version_id, len(ids))  # the only step that changes what users see
    except Exception as exc:
        log.exception("ingestion of %s %s failed; keeping previous active version", company_id, version)
        _rollback(db, store, company_id, version, version_id, str(exc))
        if isinstance(exc, InvalidPolicy):
            raise
        raise IngestionFailed(f"ingestion failed: {exc}") from exc

    return {"company_id": company_id, "version": version, "status": "active", "chunk_count": len(ids)}


def _rollback(db, store, company_id, version, version_id, error):
    try:
        store.delete_version(company_id, version)
    except Exception:
        log.exception("could not delete partial chunks for %s %s", company_id, version)
    db.mark_failed(version_id, error)


def reconcile_staging(db: MetadataDB, store: ChunkStore) -> int:
    """At startup: any version still 'staging' means a crash mid-ingestion. Mark failed + delete vectors."""
    stale = db.list_staging()
    for row in stale:
        _rollback(db, store, row["company_id"], row["version"], row["id"], "interrupted ingestion")
    return len(stale)
