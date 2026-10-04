import sqlite3

from app.storage.dashboard_db import db_connection, init_dashboard_dbs


def table_names(path):
    with sqlite3.connect(path) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }


def index_names(path):
    with sqlite3.connect(path) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }


def test_dashboard_db_initialization(tmp_path):
    paths = {
        name: tmp_path / f"{name}.db"
        for name in ("quiz", "flashcard", "productivity", "chat")
    }

    init_dashboard_dbs(
        quiz_db=paths["quiz"],
        flashcard_db=paths["flashcard"],
        productivity_db=paths["productivity"],
        chat_db=paths["chat"],
    )

    assert "quiz_attempts" in table_names(paths["quiz"])
    assert "flashcard_decks" in table_names(paths["flashcard"])
    assert {"tasks", "stickies", "pomodoro"} <= table_names(paths["productivity"])
    assert "chat_messages" in table_names(paths["chat"])
    assert "ix_chat_agent" in index_names(paths["chat"])


def test_db_connection_sets_busy_timeout(tmp_path):
    path = tmp_path / "busy.db"
    conn = db_connection(path)
    try:
        value = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.close()

    assert value == 5000
