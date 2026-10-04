"""Quiz and flashcard workspace API."""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

import config
from app.runtime.hermes import build_agent_env
from app.storage.dashboard_db import db_connection

router = APIRouter(tags=["practice"])

HERMES_PYTHON = config.HERMES_PYTHON
PROFILES_DIR = config.PROFILES_DIR
AGENT_LOG_DB = config.AGENT_LOG_DB
SUBJECTS_DIR = config.SUBJECTS_DIR
QUIZ_DB = config.QUIZ_DB
_NOTE_EXTS = {".md", ".markdown", ".txt"}
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_subject(name: str) -> Path:
    """Resolve a subject directory without allowing path traversal."""
    safe = _SAFE_NAME_RE.sub("_", name).strip("._-") or "_"
    target = (SUBJECTS_DIR / safe).resolve()
    if not target.is_relative_to(SUBJECTS_DIR.resolve()):
        raise HTTPException(status_code=400, detail="invalid subject")
    return target


def _collect_subject_notes(notes_dir: Path) -> str:
    chunks: list[str] = []
    if not notes_dir.is_dir():
        return ""
    for p in sorted(notes_dir.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in _NOTE_EXTS:
            continue
        try:
            chunks.append(
                f"\n--- {p.relative_to(notes_dir)} ---\n"
                f"{p.read_text(encoding='utf-8', errors='replace')}\n"
            )
        except OSError:
            continue
    return "".join(chunks)


def _list_generated_files(directory: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not directory.is_dir():
        return items
    for p in sorted(directory.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if not p.is_file():
            continue
        try:
            stat = p.stat()
        except OSError:
            continue
        items.append(
            {
                "filename": p.name,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
            }
        )
    return items


async def _spawn_quizmaster(task: str) -> None:
    hermes_home = PROFILES_DIR / "quizmaster"
    env = build_agent_env(
        hermes_home,
        hermes_home=hermes_home,
        agent_log_db=AGENT_LOG_DB,
    )
    provider_flag = env["HERMES_INFERENCE_PROVIDER"]
    model_flag = env["HERMES_INFERENCE_MODEL"]

    proc = await asyncio.create_subprocess_exec(
        str(HERMES_PYTHON),
        "-m",
        "hermes_cli.main",
        "-z",
        task,
        "--provider",
        provider_flag,
        "--model",
        model_flag,
        "--yolo",
        "--accept-hooks",
        env=env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


def _ensure_hermes() -> None:
    if not HERMES_PYTHON.is_file():
        raise HTTPException(
            status_code=503,
            detail=f"hermes python not found: {HERMES_PYTHON}",
        )


async def _launch_quizmaster(task: str) -> None:
    try:
        await _spawn_quizmaster(task)
    except Exception as exc:
        print(f"[practice-gen] spawn failed: {exc}")


@router.post("/api/subjects/{subject}/quiz/generate")
async def generate_subject_quiz(subject: str) -> JSONResponse:
    subject_dir = _safe_subject(subject)
    notes_dir = subject_dir / "notes"
    quizzes_dir = subject_dir / "quizzes"
    quizzes_dir.mkdir(parents=True, exist_ok=True)

    notes_text = _collect_subject_notes(notes_dir)
    if not notes_text.strip():
        raise HTTPException(status_code=400, detail="No notes found for this subject.")

    quiz_filename = f"{subject}_quiz_{int(time.time())}.md"
    quiz_path = quizzes_dir / quiz_filename
    task = (
        "Generate a quiz based on the following study notes.\n"
        f"Subject: {subject}\n"
        f"Save the quiz as a Markdown file at: {quiz_path}\n\n"
        "Instructions:\n"
        "- Include 8-15 questions.\n"
        "- Each question must be one of: multiple_choice or true_false.\n"
        "- For multiple choice, provide exactly 4 options labeled A, B, C, D.\n"
        "- For true/false, the options are True and False.\n"
        "- Mark the correct answer clearly under each question.\n"
        "- Use the following format for each question block:\n"
        "  ## Question N\n"
        "  **Type:** multiple_choice | true_false\n"
        "  <question text>\n"
        "  A. <option>\n  B. <option>\n  C. <option>\n  D. <option>\n"
        "  **Correct:** <A/B/C/D or True/False>\n"
        "  **Explanation:** <brief explanation>\n\n"
        f"Here are the notes:\n{notes_text[:8000]}"
    )

    _ensure_hermes()
    asyncio.create_task(_launch_quizmaster(task))
    return JSONResponse(
        {
            "ok": True,
            "message": f"Quizmaster is generating a quiz for {subject}. It will appear in the quiz list shortly.",
            "filename": quiz_filename,
        }
    )


@router.get("/api/subjects/{subject}/quiz")
async def list_subject_quizzes(subject: str) -> JSONResponse:
    subject_dir = _safe_subject(subject)
    return JSONResponse(
        {"subject": subject, "items": _list_generated_files(subject_dir / "quizzes")}
    )


def _parse_quiz_file(text: str) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    blocks = re.split(r"(?=^##\s+Question\s+\d+)", text, flags=re.MULTILINE)
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        q_type: str | None = None
        if re.search(r"\*\*Type:\*\*\s*multiple_choice", block, re.I):
            q_type = "multiple_choice"
        elif re.search(r"\*\*Type:\*\*\s*true_false", block, re.I):
            q_type = "true_false"

        lines = block.splitlines()
        q_text_lines: list[str] = []
        options: dict[str, str] = {}
        correct = ""
        explanation = ""
        in_question = True

        for ln in lines:
            if re.match(r"^##\s+Question\s+\d+", ln):
                in_question = True
                continue
            if re.match(r"\*\*Type:\*\*", ln):
                continue
            m = re.match(r"\*\*Explanation:\*\*\s*(.*)", ln)
            if m:
                explanation = m.group(1).strip()
                continue
            m = re.match(r"\*\*Correct:\*\*\s*(.+)", ln)
            if m:
                correct = m.group(1).strip()
                in_question = False
                continue
            opt_m = re.match(r"^([A-D])\.\s+(.+)", ln)
            if opt_m:
                options[opt_m.group(1)] = opt_m.group(2).strip()
                in_question = False
                continue
            if re.match(r"^(True|False)\.\s*", ln, re.I):
                val = ln.split(".")[0].strip()
                options[val.title()] = val.title()
                in_question = False
                continue
            if in_question:
                q_text_lines.append(ln)

        q_text = re.sub(r"\*\*", "", "\n".join(q_text_lines)).strip()
        if q_text and q_type:
            if q_type == "true_false":
                correct = correct.title()
                if not options:
                    options = {"True": "True", "False": "False"}
            questions.append(
                {
                    "question": q_text,
                    "type": q_type,
                    "options": options,
                    "correct": correct,
                    "explanation": explanation,
                }
            )
    return questions


@router.get("/api/subjects/{subject}/quiz/{filename}")
async def get_subject_quiz(subject: str, filename: str) -> JSONResponse:
    subject_dir = _safe_subject(subject)
    quizzes_dir = subject_dir / "quizzes"
    target = (quizzes_dir / filename).resolve()
    if not target.is_relative_to(quizzes_dir.resolve()):
        raise HTTPException(status_code=400, detail="invalid path")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="quiz not found")

    text = target.read_text(encoding="utf-8", errors="replace")
    return JSONResponse(
        {"subject": subject, "filename": filename, "questions": _parse_quiz_file(text)}
    )


@router.post("/api/quiz/attempt")
async def save_quiz_attempt(payload: dict[str, Any]) -> JSONResponse:
    subject = str(payload.get("subject", "")).strip()
    filename = str(payload.get("filename", "")).strip()
    try:
        score = int(payload.get("score", 0))
        total = int(payload.get("total", 0))
        time_seconds = int(payload.get("time_seconds", 0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="score, total, and time_seconds must be numbers")
    if not subject or not filename or total <= 0:
        raise HTTPException(status_code=400, detail="subject, filename, and total required")

    percentage = round((score / total) * 100.0, 1)
    conn = db_connection(QUIZ_DB)
    try:
        row = conn.execute(
            """
            INSERT INTO quiz_attempts
            (subject, filename, score, total, percentage, time_seconds, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                subject,
                filename,
                score,
                total,
                percentage,
                time_seconds,
                datetime.now(timezone.utc).isoformat(),
            ),
        ).fetchone()
        conn.commit()
    finally:
        conn.close()

    return JSONResponse(
        {
            "ok": True,
            "id": row[0],
            "subject": subject,
            "filename": filename,
            "score": score,
            "total": total,
            "percentage": percentage,
            "time_seconds": time_seconds,
        }
    )


@router.get("/api/quiz/attempts")
async def list_quiz_attempts(subject: str | None = None, limit: int = 50) -> JSONResponse:
    conn = db_connection(QUIZ_DB)
    conn.row_factory = sqlite3.Row
    try:
        if subject:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM quiz_attempts WHERE subject = ? ORDER BY created_at DESC LIMIT ?",
                    (subject, limit),
                ).fetchall()
            ]
        else:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM quiz_attempts ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            ]
        averages = [
            dict(r)
            for r in conn.execute(
                """
                SELECT subject, COUNT(*) as attempts, AVG(percentage) as avg_pct,
                       MAX(created_at) as last_attempt
                FROM quiz_attempts GROUP BY subject ORDER BY last_attempt DESC
                """
            ).fetchall()
        ]
    finally:
        conn.close()

    return JSONResponse({"items": rows, "averages": averages})


@router.post("/api/subjects/{subject}/flashcard/generate")
async def generate_subject_flashcards(subject: str) -> JSONResponse:
    subject_dir = _safe_subject(subject)
    notes_dir = subject_dir / "notes"
    flashcards_dir = subject_dir / "flashcards"
    flashcards_dir.mkdir(parents=True, exist_ok=True)

    notes_text = _collect_subject_notes(notes_dir)
    if not notes_text.strip():
        raise HTTPException(status_code=400, detail="No notes found for this subject.")

    deck_filename = f"{subject}_deck_{int(time.time())}.md"
    deck_path = flashcards_dir / deck_filename
    task = (
        "Generate a flashcard deck based on the following study notes.\n"
        f"Subject: {subject}\n"
        f"Save the deck as a Markdown file at: {deck_path}\n\n"
        "Instructions:\n"
        "- Each card is a single plain snippet — a key fact, definition, formula, or concept distilled into one or two plain sentences.\n"
        "- Include at least 15 snippets.\n"
        "- Write no headings, no numbering, no question-and-answer markers.\n"
        "- Separate every card from the next with a line containing only three dashes: ---\n"
        "- Each snippet must be self-contained and skimmable on its own.\n\n"
        f"Here are the notes:\n{notes_text[:8000]}"
    )

    asyncio.create_task(_launch_quizmaster(task))
    return JSONResponse(
        {
            "ok": True,
            "message": f"Quizmaster is generating a flashcard deck for {subject}. It will appear in the deck list shortly.",
            "filename": deck_filename,
        }
    )


@router.get("/api/subjects/{subject}/flashcard")
async def list_subject_flashcards(subject: str) -> JSONResponse:
    subject_dir = _safe_subject(subject)
    return JSONResponse(
        {"subject": subject, "items": _list_generated_files(subject_dir / "flashcards")}
    )


def _parse_flashcard_file(text: str) -> list[str]:
    cards: list[str] = []
    for block in re.split(r"^\s*---\s*$", text, flags=re.MULTILINE):
        block = block.strip()
        lines = [ln for ln in block.splitlines() if not ln.strip().startswith("#")]
        snippet = "\n".join(lines).strip()
        if snippet:
            cards.append(snippet)
    return cards


@router.get("/api/subjects/{subject}/flashcard/{filename}")
async def get_subject_flashcard(subject: str, filename: str) -> JSONResponse:
    subject_dir = _safe_subject(subject)
    flashcards_dir = subject_dir / "flashcards"
    target = (flashcards_dir / filename).resolve()
    if not target.is_relative_to(flashcards_dir.resolve()):
        raise HTTPException(status_code=400, detail="invalid path")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="deck not found")

    text = target.read_text(encoding="utf-8", errors="replace")
    cards = _parse_flashcard_file(text)
    return JSONResponse(
        {
            "subject": subject,
            "filename": filename,
            "count": len(cards),
            "cards": cards,
        }
    )
