"""
Unit tests for the upgraded Memory Engine (confidence, decay, typed memories, FTS5).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import memory


@pytest.fixture
def isolated_memory_db(tmp_path: Path, monkeypatch):
    """Fixture to redirect MEMORY_DB to a temporary directory."""
    db_file = tmp_path / "test_memory_core.db"
    monkeypatch.setattr(memory, "MEMORY_DB", db_file)
    memory._init_db()
    return db_file


def test_record_and_retrieve_with_types(isolated_memory_db: Path):
    # 1. Record memories with different semantic types & confidences
    id1 = memory.record_memory(
        "Prefers dark OLED themes and high contrast fonts",
        kind="semantic",
        mem_type="preference",
        confidence=0.95,
        salience=0.8,
        tags="ui,preference,display",
    )
    assert id1 is not None

    id2 = memory.record_memory(
        "PostgreSQL connection pooling requires pg_bouncer for high concurrency",
        kind="semantic",
        mem_type="fact",
        confidence=0.99,
        salience=0.9,
        tags="database,postgres,backend",
    )
    assert id2 is not None

    id3 = memory.record_memory(
        "Avoid using raw shell=True without permission check",
        kind="procedural",
        mem_type="error",
        confidence=0.90,
        salience=0.7,
        tags="security,runtime",
    )
    assert id3 is not None

    # 2. Retrieve all matches for 'theme'
    hits = memory.retrieve("dark theme")
    assert len(hits) >= 1
    assert hits[0]["mem_type"] == "preference"
    assert hits[0]["confidence"] == 0.95

    # 3. Retrieve filtered by mem_type
    fact_hits = memory.retrieve("postgres connection", mem_types=("fact",))
    assert len(fact_hits) >= 1
    assert fact_hits[0]["mem_type"] == "fact"

    # 4. Filter by confidence threshold
    high_conf_hits = memory.retrieve("pg_bouncer", min_confidence=0.98)
    assert len(high_conf_hits) == 1
    assert high_conf_hits[0]["confidence"] >= 0.98


def test_touch_access_decay_boost(isolated_memory_db: Path):
    id1 = memory.record_memory(
        "Quantum mechanics wave function collapse postulate",
        kind="semantic",
        mem_type="concept",
        confidence=0.9,
        decay=0.7,
    )
    assert id1 is not None

    # Touch access
    memory._touch_access([id1])

    with memory._conn() as conn:
        row = conn.execute("SELECT access_count, decay, last_access FROM memory_items WHERE id = ?", (id1,)).fetchone()
        assert row["access_count"] == 1
        assert row["decay"] == pytest.approx(0.8, rel=1e-2)  # Boosted from 0.7 + 0.1
        assert row["last_access"] is not None


def test_auto_migration_for_legacy_schema(tmp_path: Path, monkeypatch):
    """Test that a database with legacy columns automatically gets upgraded."""
    legacy_db = tmp_path / "legacy_memory.db"
    with sqlite3.connect(str(legacy_db)) as conn:
        conn.execute("""
            CREATE TABLE memory_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL DEFAULT 'episodic',
                content TEXT NOT NULL,
                summary TEXT DEFAULT '',
                source TEXT DEFAULT '',
                actor TEXT DEFAULT '',
                subject TEXT DEFAULT '',
                tags TEXT DEFAULT '',
                salience REAL NOT NULL DEFAULT 0.5,
                access_count INTEGER NOT NULL DEFAULT 0,
                embedding BLOB,
                meta TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_access TEXT
            )
        """)
        conn.commit()

    monkeypatch.setattr(memory, "MEMORY_DB", legacy_db)
    memory._init_db()

    with sqlite3.connect(str(legacy_db)) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(memory_items)").fetchall()}
        assert "mem_type" in cols
        assert "confidence" in cols
        assert "decay" in cols
