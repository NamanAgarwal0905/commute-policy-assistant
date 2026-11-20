"""SQLite: companies, demo users, and policy versions.

This is the source of truth for WHICH policy version is active. A policy_versions row
has a status: staging -> active -> inactive, or staging -> failed.
A partial unique index guarantees at most ONE active row per company, so the
"active pointer" cannot be corrupted even if the code has a bug.
"""
import hashlib
import hmac
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    username      TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    company_id    TEXT NOT NULL REFERENCES companies(id),
    role          TEXT NOT NULL CHECK (role IN ('employee', 'admin'))
);
CREATE TABLE IF NOT EXISTS policy_versions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id    TEXT NOT NULL REFERENCES companies(id),
    version       TEXT NOT NULL,
    document_name TEXT NOT NULL,
    effective_date TEXT,
    status        TEXT NOT NULL CHECK (status IN ('staging', 'active', 'inactive', 'failed')),
    chunk_count   INTEGER NOT NULL DEFAULT 0,
    error         TEXT,
    created_at    TEXT NOT NULL,
    UNIQUE (company_id, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_per_company
    ON policy_versions(company_id) WHERE status = 'active';
"""


class VersionExists(Exception):
    """This company already has a non-failed policy with this version label."""


def hash_password(username: str, password: str) -> str:
    # Demo-grade hashing (the username is the salt). A real system uses an identity provider.
    return hashlib.pbkdf2_hmac("sha256", password.encode(), username.encode(), 100_000).hex()


class MetadataDB:
    def __init__(self, path: str):
        self.path = path
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        # One short-lived connection per operation: simple and thread-safe.
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:  # commit on success, rollback on exception
                yield conn
        finally:
            conn.close()

    # ---------- companies and users ----------
    def add_company(self, company_id: str, name: str) -> None:
        with self._conn() as c:
            c.execute("INSERT OR IGNORE INTO companies(id, name) VALUES (?, ?)", (company_id, name))

    def company_exists(self, company_id: str) -> bool:
        with self._conn() as c:
            return c.execute("SELECT 1 FROM companies WHERE id = ?", (company_id,)).fetchone() is not None

    def add_user(self, username: str, password: str, company_id: str, role: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO users(username, password_hash, company_id, role) VALUES (?, ?, ?, ?)",
                (username, hash_password(username, password), company_id, role),
            )

    def get_user(self, username: str) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None

    def check_login(self, username: str, password: str) -> Optional[dict]:
        user = self.get_user(username)
        if user is None:
            return None
        if not hmac.compare_digest(user["password_hash"], hash_password(username, password)):
            return None
        return user

    # ---------- policy versions ----------
    def get_active_version(self, company_id: str) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM policy_versions WHERE company_id = ? AND status = 'active'", (company_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_versions(self, company_id: str) -> list:
        with self._conn() as c:
            rows = c.execute(
                "SELECT version, document_name, effective_date, status, chunk_count, error, created_at "
                "FROM policy_versions WHERE company_id = ? ORDER BY id",
                (company_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_staging(self) -> list:
        with self._conn() as c:
            rows = c.execute("SELECT id, company_id, version FROM policy_versions WHERE status = 'staging'").fetchall()
        return [dict(r) for r in rows]

    def create_staged(self, company_id: str, version: str, document_name: str, effective_date: Optional[str]) -> int:
        """Register a new version as 'staging'. A previous *failed* attempt may be retried."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as c:
            existing = c.execute(
                "SELECT status FROM policy_versions WHERE company_id = ? AND version = ?", (company_id, version)
            ).fetchone()
            if existing is not None:
                if existing["status"] != "failed":
                    raise VersionExists(f"{company_id} already has version {version} ({existing['status']})")
                c.execute("DELETE FROM policy_versions WHERE company_id = ? AND version = ?", (company_id, version))
            cur = c.execute(
                "INSERT INTO policy_versions(company_id, version, document_name, effective_date, status, created_at) "
                "VALUES (?, ?, ?, ?, 'staging', ?)",
                (company_id, version, document_name, effective_date, now),
            )
            return cur.lastrowid

    def mark_failed(self, version_id: int, error: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE policy_versions SET status = 'failed', error = ? WHERE id = ?", (error[:500], version_id)
            )

    def activate(self, version_id: int, chunk_count: int) -> None:
        """Flip the active version in ONE transaction: old -> inactive, new -> active."""
        with self._conn() as c:
            row = c.execute("SELECT company_id, status FROM policy_versions WHERE id = ?", (version_id,)).fetchone()
            if row is None or row["status"] != "staging":
                raise ValueError("only a staging version can be activated")
            c.execute(
                "UPDATE policy_versions SET status = 'inactive' WHERE company_id = ? AND status = 'active'",
                (row["company_id"],),
            )
            c.execute(
                "UPDATE policy_versions SET status = 'active', chunk_count = ? WHERE id = ?",
                (chunk_count, version_id),
            )
