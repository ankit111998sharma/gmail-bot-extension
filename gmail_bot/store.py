from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS inbox_processed (
    message_id TEXT PRIMARY KEY,
    thread_id TEXT,
    draft_id TEXT,
    processed_at TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS draft_queue (
    message_id TEXT PRIMARY KEY,
    thread_id TEXT,
    sender TEXT,
    subject TEXT,
    snippet TEXT,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    draft_preview TEXT,
    draft_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sent_style_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT,
    body TEXT NOT NULL,
    language TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    body TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    event TEXT NOT NULL,
    payload TEXT
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def already_processed(self, message_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM inbox_processed WHERE message_id = ?", (message_id,)
        ).fetchone()
        return row is not None

    def mark_processed(self, message_id: str, thread_id: str, draft_id: str, status: str = "drafted") -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO inbox_processed
                (message_id, thread_id, draft_id, processed_at, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (message_id, thread_id, draft_id, utcnow(), status),
        )
        self._conn.commit()

    def processed_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM inbox_processed").fetchone()
        return int(row["n"]) if row else 0

    def upsert_queue(self, row: dict[str, Any]) -> None:
        now = utcnow()
        self._conn.execute(
            """
            INSERT INTO draft_queue (
                message_id, thread_id, sender, subject, snippet, status, attempts,
                last_error, draft_preview, draft_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(message_id) DO UPDATE SET
                thread_id = excluded.thread_id,
                sender = excluded.sender,
                subject = excluded.subject,
                snippet = excluded.snippet,
                status = excluded.status,
                attempts = excluded.attempts,
                last_error = excluded.last_error,
                draft_preview = excluded.draft_preview,
                draft_id = excluded.draft_id,
                updated_at = excluded.updated_at
            """,
            (
                row["message_id"],
                row.get("thread_id"),
                row.get("sender"),
                row.get("subject"),
                row.get("snippet"),
                row.get("status", "pending"),
                row.get("attempts", 0),
                row.get("last_error"),
                row.get("draft_preview"),
                row.get("draft_id"),
                row.get("created_at", now),
                now,
            ),
        )
        self._conn.commit()

    def get_queue_item(self, message_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM draft_queue WHERE message_id = ?", (message_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_queue(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM draft_queue ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def queue_counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM draft_queue GROUP BY status"
        ).fetchall()
        return {str(r["status"]): int(r["n"]) for r in rows}

    def replace_knowledge(self, chunks: list[tuple[str, str, str]]) -> None:
        self._conn.execute("DELETE FROM knowledge_chunks")
        now = utcnow()
        self._conn.executemany(
            "INSERT INTO knowledge_chunks (id, source, text, created_at) VALUES (?, ?, ?, ?)",
            [(cid, source, text, now) for cid, source, text in chunks],
        )
        self._conn.commit()

    def list_knowledge(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, source, text FROM knowledge_chunks ORDER BY source, id"
        ).fetchall()
        return [dict(r) for r in rows]

    def replace_style_examples(self, examples: list[tuple[str, str, str]]) -> None:
        self._conn.execute("DELETE FROM sent_style_examples")
        now = utcnow()
        self._conn.executemany(
            "INSERT INTO sent_style_examples (subject, body, language, fetched_at) VALUES (?, ?, ?, ?)",
            [(subject, body, language, now) for subject, body, language in examples],
        )
        self._conn.commit()

    def list_style_examples(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT subject, body, language FROM sent_style_examples ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def upsert_template(self, name: str, body: str) -> None:
        now = utcnow()
        self._conn.execute(
            """
            INSERT INTO templates (name, body, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET body = excluded.body, updated_at = excluded.updated_at
            """,
            (name, body, now),
        )
        self._conn.commit()

    def list_templates(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, name, body, updated_at FROM templates ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_template(self, name: str) -> None:
        self._conn.execute("DELETE FROM templates WHERE name = ?", (name,))
        self._conn.commit()

    def add_job_log(self, level: str, event: str, payload: str | None = None) -> None:
        self._conn.execute(
            "INSERT INTO job_log (ts, level, event, payload) VALUES (?, ?, ?, ?)",
            (utcnow(), level, event, payload),
        )
        self._conn.commit()

    def set_setting(self, key: str, value: str) -> None:
        self._conn.execute(
            """
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        self._conn.commit()

    def get_setting(self, key: str, default: str = "") -> str:
        row = self._conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row and row["value"] is not None else default

    def recent_job_logs(self, limit: int = 40) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT ts, level, event, payload FROM job_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
