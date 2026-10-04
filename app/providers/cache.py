"""
SQLite-backed response caching for LLM completions.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class ResponseCache:
    """Thread-safe SQLite response cache with TTL and hit tracking."""

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = Path(__file__).resolve().parent.parent.parent / "logs" / "llm_cache.db"
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.db_path), timeout=10.0) as conn:
            conn.execute("PRAGMA journal_mode=TRUNCATE")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS response_cache (
                    cache_key TEXT PRIMARY KEY,
                    response_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    ttl_seconds REAL NOT NULL,
                    hits INTEGER DEFAULT 0
                )
                """
            )
            conn.commit()

    @staticmethod
    def compute_key(task_type: str, model: str, prompt: str) -> str:
        raw = f"{task_type}:{model}:{prompt.strip()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, task_type: str, model: str, prompt: str) -> dict[str, Any] | None:
        """Retrieve cached response if available and not expired."""
        key = self.compute_key(task_type, model, prompt)
        try:
            with sqlite3.connect(str(self.db_path), timeout=5.0) as conn:
                row = conn.execute(
                    "SELECT response_json, created_at, ttl_seconds FROM response_cache WHERE cache_key=?",
                    (key,),
                ).fetchone()
                if not row:
                    return None

                response_json, created_at, ttl_seconds = row
                if time.time() - created_at < ttl_seconds:
                    conn.execute(
                        "UPDATE response_cache SET hits = hits + 1 WHERE cache_key=?",
                        (key,),
                    )
                    conn.commit()
                    return json.loads(response_json)
                else:
                    # Expired entry, remove it
                    conn.execute("DELETE FROM response_cache WHERE cache_key=?", (key,))
                    conn.commit()
                    return None
        except Exception:
            return None

    def set(
        self,
        task_type: str,
        model: str,
        prompt: str,
        response_dict: dict[str, Any],
        ttl_seconds: float = 3600.0,
    ) -> None:
        """Store a response in cache with specified TTL."""
        key = self.compute_key(task_type, model, prompt)
        try:
            with sqlite3.connect(str(self.db_path), timeout=5.0) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO response_cache (cache_key, response_json, created_at, ttl_seconds, hits)
                    VALUES (?, ?, ?, ?, 0)
                    """,
                    (key, json.dumps(response_dict), time.time(), ttl_seconds),
                )
                conn.commit()
        except Exception:
            pass

    def clear_expired(self) -> int:
        """Purge stale cache entries."""
        try:
            with sqlite3.connect(str(self.db_path), timeout=5.0) as conn:
                cursor = conn.execute(
                    "DELETE FROM response_cache WHERE (? - created_at) >= ttl_seconds",
                    (time.time(),),
                )
                conn.commit()
                return cursor.rowcount
        except Exception:
            return 0
