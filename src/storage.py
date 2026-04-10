from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any


class HistoryStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                    action TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    source_kind TEXT NOT NULL DEFAULT 'manual',
                    direction TEXT NOT NULL DEFAULT 'auto',
                    source_text TEXT NOT NULL,
                    result_text TEXT NOT NULL,
                    source_norm TEXT NOT NULL DEFAULT '',
                    source_hash TEXT NOT NULL DEFAULT '',
                    favorite INTEGER NOT NULL DEFAULT 0,
                    use_count INTEGER NOT NULL DEFAULT 1,
                    last_used_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                )
                """
            )
            self._ensure_columns(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_created ON history(created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_favorite ON history(favorite, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_mode ON history(mode)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_direction ON history(direction)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_hash ON history(source_hash)")
            conn.commit()

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("PRAGMA table_info(history)").fetchall()
        names = {str(row["name"]) for row in rows}
        required = {
            "source_kind": "TEXT NOT NULL DEFAULT 'manual'",
            "direction": "TEXT NOT NULL DEFAULT 'auto'",
            "source_norm": "TEXT NOT NULL DEFAULT ''",
            "source_hash": "TEXT NOT NULL DEFAULT ''",
            "favorite": "INTEGER NOT NULL DEFAULT 0",
            "use_count": "INTEGER NOT NULL DEFAULT 1",
            # SQLite ALTER TABLE only allows constant defaults, so we backfill after adding.
            "last_used_at": "TEXT NOT NULL DEFAULT ''",
        }
        for name, schema in required.items():
            if name in names:
                continue
            conn.execute(f"ALTER TABLE history ADD COLUMN {name} {schema}")
        conn.execute(
            """
            UPDATE history
            SET last_used_at = COALESCE(NULLIF(last_used_at, ''), created_at, datetime('now', 'localtime'))
            WHERE last_used_at IS NULL OR last_used_at = ''
            """
        )

    @staticmethod
    def _normalize_text(text: str) -> str:
        value = " ".join(str(text or "").strip().lower().split())
        return value[:4000]

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()

    def add_record(
        self,
        action: str,
        mode: str,
        source_text: str,
        result_text: str,
        *,
        source_kind: str = "manual",
        direction: str = "auto",
    ) -> int:
        mode = self._normalize_mode(mode)
        source_norm = self._normalize_text(source_text)
        source_hash = self._hash_text(source_norm) if source_norm else ""
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT id, use_count
                FROM history
                WHERE source_hash = ? AND mode = ? AND direction = ? AND source_kind = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (source_hash, mode, direction, source_kind),
            ).fetchone()
            if existing is not None and source_hash:
                conn.execute(
                    """
                    UPDATE history
                    SET action = ?,
                        source_text = ?,
                        result_text = ?,
                        use_count = ?,
                        last_used_at = datetime('now', 'localtime'),
                        created_at = datetime('now', 'localtime')
                    WHERE id = ?
                    """,
                    (action, source_text, result_text, int(existing["use_count"] or 0) + 1, int(existing["id"])),
                )
                record_id = int(existing["id"])
            else:
                cur = conn.execute(
                    """
                    INSERT INTO history (
                        action, mode, source_kind, direction,
                        source_text, result_text, source_norm, source_hash,
                        favorite, use_count, last_used_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 1, datetime('now', 'localtime'))
                    """,
                    (action, mode, source_kind, direction, source_text, result_text, source_norm, source_hash),
                )
                record_id = int(cur.lastrowid or 0)
            conn.commit()
            return record_id

    def list_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        result = self.list_records(limit=max(1, int(limit)), offset=0)
        return result["items"]

    def list_records(
        self,
        *,
        tab: str = "recent",
        q: str = "",
        mode: str = "all",
        direction: str = "all",
        source_kind: str = "all",
        range_days: int = 0,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        where_parts: list[str] = []
        args: list[Any] = []
        if tab == "favorites":
            where_parts.append("favorite = 1")
        normalized_mode = self._normalize_mode(mode) if mode != "all" else "all"
        if normalized_mode == "ai":
            where_parts.append("mode = ?")
            args.append("ai")
        elif normalized_mode == "dictionary":
            where_parts.append("mode = ?")
            args.append("dictionary")
        if direction and direction != "all":
            where_parts.append("direction = ?")
            args.append(direction)
        if source_kind and source_kind != "all":
            where_parts.append("source_kind = ?")
            args.append(source_kind)
        if range_days in {7, 30, 90}:
            where_parts.append("created_at >= datetime('now', 'localtime', ?)")
            args.append(f"-{range_days} day")
        if q.strip():
            keyword = f"%{q.strip()}%"
            where_parts.append("(source_text LIKE ? OR result_text LIKE ?)")
            args.extend([keyword, keyword])

        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        # Keep "recent" stable by recency/use, do not promote favorites to top.
        order_sql = "ORDER BY last_used_at DESC, id DESC"
        safe_limit = max(1, min(200, int(limit)))
        safe_offset = max(0, int(offset))

        with self._connect() as conn:
            total_row = conn.execute(
                f"SELECT COUNT(1) AS total FROM history {where_sql}",
                tuple(args),
            ).fetchone()
            rows = conn.execute(
                f"""
                SELECT
                    id, created_at, action, mode, source_kind, direction,
                    source_text, result_text, favorite, use_count, last_used_at
                FROM history
                {where_sql}
                {order_sql}
                LIMIT ? OFFSET ?
                """,
                tuple([*args, safe_limit, safe_offset]),
            ).fetchall()

        items = [
            {
                "id": int(row["id"]),
                "created_at": str(row["created_at"] or ""),
                "action": str(row["action"] or ""),
                "mode": self._normalize_mode(str(row["mode"] or "")),
                "source_kind": str(row["source_kind"] or "manual"),
                "direction": str(row["direction"] or "auto"),
                "source_text": str(row["source_text"] or ""),
                "result_text": str(row["result_text"] or ""),
                "favorite": bool(int(row["favorite"] or 0)),
                "use_count": int(row["use_count"] or 0),
                "last_used_at": str(row["last_used_at"] or row["created_at"] or ""),
            }
            for row in rows
        ]
        total = int(total_row["total"] if total_row and "total" in total_row.keys() else 0)
        return {
            "items": items,
            "total": total,
            "offset": safe_offset,
            "limit": safe_limit,
            "has_more": (safe_offset + len(items)) < total,
        }

    def set_favorite(self, record_id: int, favorite: bool) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE history SET favorite = ? WHERE id = ?",
                (1 if favorite else 0, int(record_id)),
            )
            conn.commit()
            return cur.rowcount > 0

    def increment_use_count(self, record_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE history
                SET use_count = use_count + 1,
                    last_used_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (int(record_id),),
            )
            conn.commit()
            return cur.rowcount > 0

    def delete_record(self, record_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM history WHERE id = ?", (int(record_id),))
            conn.commit()
            return cur.rowcount > 0

    def prune_older_than(self, days: int) -> int:
        if days <= 0:
            return 0
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM history WHERE created_at < datetime('now', 'localtime', ?)",
                (f"-{int(days)} day",),
            )
            conn.commit()
            return int(cur.rowcount or 0)

    def clear_non_favorite(self) -> int:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM history WHERE favorite = 0")
            conn.commit()
            return int(cur.rowcount or 0)

    def clear_favorites(self) -> int:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM history WHERE favorite = 1")
            conn.commit()
            return int(cur.rowcount or 0)

    def distinct_directions(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT direction
                FROM history
                WHERE direction IS NOT NULL AND direction != ''
                ORDER BY direction ASC
                """,
            ).fetchall()
        return [str(row["direction"]) for row in rows if row["direction"]]

    def clear(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM history")
            conn.commit()
    @staticmethod
    def _normalize_mode(mode: str | None) -> str:
        value = str(mode or "dictionary").strip().lower()
        return value if value in {"dictionary", "ai"} else "dictionary"
