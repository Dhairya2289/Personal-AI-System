import sqlite3

import pytest

import app.practice_api as practice


def test_parse_quiz_file():
    text = """## Question 1
**Type:** multiple_choice
What is 2 + 2?
A. 3
B. 4
C. 5
D. 6
**Correct:** B
**Explanation:** Addition.

## Question 2
**Type:** true_false
The Earth is round.
**Correct:** True
**Explanation:** Basic fact.
"""
    questions = practice._parse_quiz_file(text)

    assert len(questions) == 2
    assert questions[0]["correct"] == "B"
    assert questions[0]["options"]["B"] == "4"
    assert questions[1]["options"] == {"True": "True", "False": "False"}


def test_parse_flashcard_file():
    text = """# Deck
First fact

---

# Another heading
Second fact
"""
    assert practice._parse_flashcard_file(text) == ["First fact", "Second fact"]


def test_safe_subject_blocks_traversal(monkeypatch, tmp_path):
    subjects = tmp_path / "subjects"
    subjects.mkdir()
    monkeypatch.setattr(practice, "SUBJECTS_DIR", subjects)

    safe = practice._safe_subject("../../physics")
    assert safe.parent == subjects
    assert safe.name == "physics"

    # Sanitization should keep the result inside the configured subjects root.
    assert safe.is_relative_to(subjects)


@pytest.mark.asyncio
async def test_save_quiz_attempt(monkeypatch, tmp_path):
    quiz_db = tmp_path / "quiz.db"
    with sqlite3.connect(quiz_db) as conn:
        conn.execute(
            """
            CREATE TABLE quiz_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT NOT NULL,
                filename TEXT NOT NULL,
                score INTEGER NOT NULL,
                total INTEGER NOT NULL,
                percentage REAL NOT NULL,
                time_seconds INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()

    monkeypatch.setattr(practice, "QUIZ_DB", quiz_db)

    response = await practice.save_quiz_attempt(
        {
            "subject": "Physics",
            "filename": "kinematics_quiz.md",
            "score": 8,
            "total": 10,
            "time_seconds": 42,
        }
    )

    assert response.status_code == 200
    payload = response.body.decode()
    assert '"percentage": 80.0' in payload
