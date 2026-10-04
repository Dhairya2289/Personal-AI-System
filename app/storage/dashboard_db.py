"""Dashboard-owned SQLite storage helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def db_connection(path: Path, *, timeout: float = 10.0) -> sqlite3.Connection:
    """Open a dashboard DB with consistent lock-wait behavior."""
    conn = sqlite3.connect(str(path), timeout=timeout)
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_quiz_db(path: Path) -> None:
    conn = db_connection(path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS quiz_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT NOT NULL,
                filename TEXT NOT NULL,
                score INTEGER NOT NULL,
                total INTEGER NOT NULL,
                percentage REAL NOT NULL,
                time_seconds INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def init_flashcard_db(path: Path) -> None:
    conn = db_connection(path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS flashcard_decks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT NOT NULL,
                filename TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def init_productivity_db(path: Path) -> None:
    conn = db_connection(path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                subject TEXT,
                status TEXT NOT NULL DEFAULT 'todo',
                position REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS stickies (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                color TEXT NOT NULL DEFAULT 'amber',
                position REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pomodoro (
                day TEXT PRIMARY KEY,
                count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def init_chat_db(path: Path) -> None:
    conn = db_connection(path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT NOT NULL,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_chat_agent ON chat_messages(agent, created_at)"
        )
        conn.commit()
    finally:
        conn.close()


def init_dashboard_dbs(
    *,
    quiz_db: Path,
    flashcard_db: Path,
    productivity_db: Path,
    chat_db: Path,
) -> None:
    """Create all dashboard-owned tables if they do not already exist."""
    init_quiz_db(quiz_db)
    init_flashcard_db(flashcard_db)
    init_productivity_db(productivity_db)
    init_chat_db(chat_db)
