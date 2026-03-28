from __future__ import annotations

import sqlite3
from pathlib import Path


class HistoryStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                    action TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    source_text TEXT NOT NULL,
                    result_text TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def add_record(self, action: str, mode: str, source_text: str, result_text: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO history (action, mode, source_text, result_text)
                VALUES (?, ?, ?, ?)
                """,
                (action, mode, source_text, result_text),
            )
            conn.commit()

    def list_recent(self, limit: int = 100) -> list[dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT created_at, action, mode, source_text, result_text
                FROM history
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [
            {
                "created_at": row[0],
                "action": row[1],
                "mode": row[2],
                "source_text": row[3],
                "result_text": row[4],
            }
            for row in rows
        ]

    def clear(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM history")
            conn.commit()
