"""
Mission Control — stats router (additive module).

Read-only, Observable-Plot-friendly JSON drawn from the existing study DBs.
Every endpoint opens its SQLite source with mode=ro and degrades to an empty
list / zeroed summary if a DB, table, or column is missing — it never writes
and never crashes the dashboard.

Data sources (all read-only):
  quiz.db        -> quiz_attempts(subject, filename, score, total,
                                  percentage, time_seconds, created_at)
  tracker.db     -> tracker_days(date, data JSON{ totalStudyMinutes,
                                  studyBlocksCompleted[], mcqEntries[] })
  productivity.db-> pomodoro(day, count, updated_at)
  tracker_plan.json -> plan blocks {id, subject, durationMinutes} used to
                       resolve completed study-block ids back to minutes/subject

Self-contained APIRouter, mounted by main.py with a single include line.
Routes:
  GET /api/stats/quiz         -> [{topic, subject, score, ts}]
  GET /api/stats/productivity -> [{ts, minutes, subject, kind}]
  GET /api/stats/summary      -> {bySubject:[...], streak, totals}
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import config

router = APIRouter()

# Same paths main.py uses (resolved centrally in config.py).
QUIZ_DB = config.QUIZ_DB
TRACKER_DB = config.TRACKER_DB
PRODUCTIVITY_DB = config.PRODUCTIVITY_DB
PLAN_FILE = config.PLAN_FILE

# Minutes credited per completed pomodoro (dashboard uses 25-min focus blocks).
POMODORO_MINUTES = 25

ROADMAP_META = config.ROADMAP_META


def _load_subject_labels() -> dict[str, str]:
    """Subject display labels, sourced from the JEE-2027 roadmap so this page
    stays in sync with the tracker. Falls back to the built-in map if the
    roadmap meta isn't present yet."""
    try:
        meta = json.loads(ROADMAP_META.read_text(encoding="utf-8"))
        labels = {k: v["label"] for k, v in meta.get("subjects", {}).items()}
        if labels:
            return labels
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {"phy": "Physics", "chem": "Chemistry", "math": "Mathematics",
            "bio": "Biology", "review": "Drill / Revision", "mock": "Mocks",
            "admin": "Admin / Exams"}


SUBJECT_LABELS = _load_subject_labels()


def _ro_connect(path: Path) -> sqlite3.Connection | None:
    """Open a DB read-only; return None if it can't be opened."""
    if not path.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.Error:
        return None


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    try:
        row = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


# Resolve completed-study-block ids -> {subject, durationMinutes} via the plan.
_PLAN_BY_ID: dict[str, dict] | None = None


def _plan_by_id() -> dict[str, dict]:
    global _PLAN_BY_ID
    if _PLAN_BY_ID is not None:
        return _PLAN_BY_ID
    out: dict[str, dict] = {}
    try:
        raw = json.loads(PLAN_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            for b in raw:
                bid = b.get("id")
                if bid:
                    out[str(bid)] = b
    except Exception:  # noqa: BLE001 — no plan == no resolution, fine
        out = {}
    _PLAN_BY_ID = out
    return out


@router.get("/api/stats/quiz")
async def stats_quiz() -> JSONResponse:
    """One row per quiz attempt: {topic, subject, score, ts}.

    `topic` is the deck/quiz filename (no separate topic column exists);
    `score` is the percentage (0-100) for chart-friendliness, with raw
    score/total carried alongside.
    """
    con = _ro_connect(QUIZ_DB)
    if con is None:
        return JSONResponse([])
    rows_out: list[dict] = []
    try:
        if _table_exists(con, "quiz_attempts"):
            for r in con.execute(
                "SELECT subject, filename, score, total, percentage, "
                "time_seconds, created_at FROM quiz_attempts ORDER BY created_at"
            ):
                fname = r["filename"] or ""
                topic = Path(fname).stem.replace("_", " ").replace("-", " ").strip() or fname
                pct = r["percentage"]
                if pct is None and r["total"]:
                    pct = round(100.0 * (r["score"] or 0) / r["total"], 1)
                rows_out.append(
                    {
                        "topic": topic,
                        "subject": SUBJECT_LABELS.get(r["subject"], r["subject"]),
                        "subjectKey": r["subject"],
                        "score": round(float(pct), 1) if pct is not None else 0.0,
                        "raw": r["score"],
                        "total": r["total"],
                        "timeSeconds": r["time_seconds"],
                        "ts": r["created_at"],
                    }
                )
    except sqlite3.Error:
        rows_out = []
    finally:
        con.close()
    return JSONResponse(rows_out)


@router.get("/api/stats/productivity")
async def stats_productivity() -> JSONResponse:
    """Pomodoro / study sessions: [{ts, minutes, subject, kind}].

    Three kinds are emitted:
      - "study"    : one row per completed study block (minutes & subject
                     resolved from tracker_plan.json), per tracker day.
      - "study-day": fallback single row of totalStudyMinutes for a day when
                     no individual blocks could be resolved.
      - "pomodoro" : daily pomodoro count from productivity.db (× 25 min).
    """
    out: list[dict] = []
    plan = _plan_by_id()

    # --- study sessions from tracker_days JSON ---
    con = _ro_connect(TRACKER_DB)
    if con is not None:
        try:
            if _table_exists(con, "tracker_days"):
                for r in con.execute(
                    "SELECT date, data FROM tracker_days ORDER BY date"
                ):
                    day = r["date"]
                    try:
                        d = json.loads(r["data"]) if r["data"] else {}
                    except (json.JSONDecodeError, TypeError):
                        d = {}
                    completed = d.get("studyBlocksCompleted") or []
                    emitted_minutes = 0
                    if isinstance(completed, list):
                        for bid in completed:
                            blk = plan.get(str(bid))
                            if not blk:
                                continue
                            mins = blk.get("durationMinutes") or 0
                            subj = blk.get("subject", "")
                            out.append(
                                {
                                    "ts": day,
                                    "minutes": int(mins),
                                    "subject": SUBJECT_LABELS.get(subj, subj),
                                    "subjectKey": subj,
                                    "kind": "study",
                                }
                            )
                            emitted_minutes += int(mins)
                    # Fallback: account for any logged minutes not covered by blocks.
                    total_min = d.get("totalStudyMinutes") or 0
                    try:
                        total_min = int(total_min)
                    except (TypeError, ValueError):
                        total_min = 0
                    leftover = total_min - emitted_minutes
                    if leftover > 0:
                        out.append(
                            {
                                "ts": day,
                                "minutes": leftover,
                                "subject": "Study",
                                "subjectKey": "",
                                "kind": "study-day",
                            }
                        )
        except sqlite3.Error:
            pass
        finally:
            con.close()

    # --- pomodoro counts from productivity.db ---
    con = _ro_connect(PRODUCTIVITY_DB)
    if con is not None:
        try:
            if _table_exists(con, "pomodoro"):
                for r in con.execute(
                    "SELECT day, count FROM pomodoro ORDER BY day"
                ):
                    cnt = r["count"] or 0
                    if cnt <= 0:
                        continue
                    out.append(
                        {
                            "ts": r["day"],
                            "minutes": int(cnt) * POMODORO_MINUTES,
                            "subject": "Pomodoro",
                            "subjectKey": "",
                            "kind": "pomodoro",
                            "count": int(cnt),
                        }
                    )
        except sqlite3.Error:
            pass
        finally:
            con.close()

    out.sort(key=lambda x: (x.get("ts") or "", x.get("kind") or ""))
    return JSONResponse(out)


def _compute_streak(days_with_activity: set[str]) -> int:
    """Consecutive days (ending today or yesterday) with any logged activity."""
    if not days_with_activity:
        return 0
    today = date.today()
    # Allow the streak to "still be alive" if today has no entry yet.
    start = today if today.isoformat() in days_with_activity else today - timedelta(days=1)
    streak = 0
    cur = start
    while cur.isoformat() in days_with_activity:
        streak += 1
        cur -= timedelta(days=1)
    return streak


@router.get("/api/stats/summary")
async def stats_summary() -> JSONResponse:
    """Aggregate rollup: {bySubject:[...], streak, totals}.

    bySubject = per-subject {subject, quizAttempts, avgScore, studyMinutes}.
    streak    = consecutive active days (study minutes or quiz attempts).
    totals    = headline counters across everything.
    """
    plan = _plan_by_id()
    by_subject: dict[str, dict] = {}

    def bucket(key: str) -> dict:
        label = SUBJECT_LABELS.get(key, key) if key else "Other"
        return by_subject.setdefault(
            key or "_other",
            {
                "subject": label,
                "subjectKey": key,
                "quizAttempts": 0,
                "scoreSum": 0.0,
                "studyMinutes": 0,
            },
        )

    active_days: set[str] = set()
    total_quiz = 0
    total_study_minutes = 0
    total_pomodoros = 0
    total_mcq = 0

    # quizzes
    con = _ro_connect(QUIZ_DB)
    if con is not None:
        try:
            if _table_exists(con, "quiz_attempts"):
                for r in con.execute(
                    "SELECT subject, score, total, percentage, created_at "
                    "FROM quiz_attempts"
                ):
                    total_quiz += 1
                    b = bucket(r["subject"] or "")
                    b["quizAttempts"] += 1
                    pct = r["percentage"]
                    if pct is None and r["total"]:
                        pct = 100.0 * (r["score"] or 0) / r["total"]
                    b["scoreSum"] += float(pct or 0.0)
                    if r["created_at"]:
                        active_days.add(str(r["created_at"])[:10])
        except sqlite3.Error:
            pass
        finally:
            con.close()

    # study minutes + mcq from tracker
    con = _ro_connect(TRACKER_DB)
    if con is not None:
        try:
            if _table_exists(con, "tracker_days"):
                for r in con.execute("SELECT date, data FROM tracker_days"):
                    day = r["date"]
                    try:
                        d = json.loads(r["data"]) if r["data"] else {}
                    except (json.JSONDecodeError, TypeError):
                        d = {}
                    completed = d.get("studyBlocksCompleted") or []
                    accounted = 0
                    if isinstance(completed, list):
                        for bid in completed:
                            blk = plan.get(str(bid))
                            if not blk:
                                continue
                            mins = int(blk.get("durationMinutes") or 0)
                            bucket(blk.get("subject", "") or "")["studyMinutes"] += mins
                            accounted += mins
                    try:
                        tot = int(d.get("totalStudyMinutes") or 0)
                    except (TypeError, ValueError):
                        tot = 0
                    leftover = max(0, tot - accounted)
                    bucket("")["studyMinutes"] += leftover
                    total_study_minutes += max(tot, accounted)
                    mcqs = d.get("mcqEntries") or []
                    if isinstance(mcqs, list):
                        for e in mcqs:
                            try:
                                total_mcq += int(e.get("attempted", 0) or 0)
                            except (TypeError, ValueError, AttributeError):
                                pass
                    if (tot > 0) or completed or mcqs:
                        active_days.add(str(day)[:10])
        except sqlite3.Error:
            pass
        finally:
            con.close()

    # pomodoros
    con = _ro_connect(PRODUCTIVITY_DB)
    if con is not None:
        try:
            if _table_exists(con, "pomodoro"):
                for r in con.execute("SELECT day, count FROM pomodoro"):
                    cnt = int(r["count"] or 0)
                    total_pomodoros += cnt
                    if cnt > 0 and r["day"]:
                        active_days.add(str(r["day"])[:10])
        except sqlite3.Error:
            pass
        finally:
            con.close()

    by_subject_list = []
    for v in by_subject.values():
        attempts = v["quizAttempts"]
        by_subject_list.append(
            {
                "subject": v["subject"],
                "subjectKey": v["subjectKey"],
                "quizAttempts": attempts,
                "avgScore": round(v["scoreSum"] / attempts, 1) if attempts else 0.0,
                "studyMinutes": v["studyMinutes"],
            }
        )
    by_subject_list.sort(key=lambda x: (-x["studyMinutes"], -x["quizAttempts"], x["subject"]))

    summary = {
        "bySubject": by_subject_list,
        "streak": _compute_streak(active_days),
        "totals": {
            "quizAttempts": total_quiz,
            "studyMinutes": total_study_minutes,
            "studyHours": round(total_study_minutes / 60.0, 1),
            "pomodoros": total_pomodoros,
            "mcqAttempted": total_mcq,
            "activeDays": len(active_days),
        },
    }
    return JSONResponse(summary)


@router.get("/api/stats/executive-overview")
async def executive_overview() -> JSONResponse:
    """Consolidated Personal AI Mission Control overview card:
    - Today's completion percentage & streak
    - Subject study hours breakdown
    - Daily task progress
    - Memory footprint & confidence
    - AI ops metrics
    """
    today_str = date.today().isoformat()
    plan = _plan_by_id()

    today_minutes_by_subject: dict[str, int] = defaultdict(int)
    today_score = 0
    completed_tasks = 0
    total_tasks = 0

    # 1. Fetch tracker state for today
    con = _ro_connect(TRACKER_DB)
    if con is not None:
        try:
            if _table_exists(con, "tracker_days"):
                row = con.execute("SELECT data FROM tracker_days WHERE date = ?", (today_str,)).fetchone()
                if row and row["data"]:
                    try:
                        d = json.loads(row["data"])
                        today_score = int(d.get("disciplineScore", 0) or 0)
                        blocks = d.get("studyBlocksCompleted", [])
                        if isinstance(blocks, list):
                            completed_tasks = len(blocks)
                            for bid in blocks:
                                blk = plan.get(str(bid))
                                if blk:
                                    subj = blk.get("subject", "other")
                                    mins = int(blk.get("durationMinutes", 0) or 0)
                                    today_minutes_by_subject[subj] += mins
                    except Exception:
                        pass
        except sqlite3.Error:
            pass
        finally:
            con.close()

    # 2. Memory stats
    import memory
    total_memories = 0
    avg_conf = 0.8
    by_mem_type: dict[str, int] = {}
    try:
        mcon = memory._conn()
        total_memories = mcon.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0]
        avg_row = mcon.execute("SELECT AVG(confidence) FROM memory_items").fetchone()[0]
        avg_conf = float(avg_row) if avg_row is not None else 0.85
        for r in mcon.execute("SELECT mem_type, COUNT(*) FROM memory_items GROUP BY mem_type"):
            by_mem_type[r[0]] = r[1]
        mcon.close()
    except Exception:
        pass

    # 3. AI ops summary
    from app.providers.manager import get_provider_manager
    pm = get_provider_manager()
    cb_status = await pm.breaker.get_status()
    all_healthy = all(s.get("state") == "CLOSED" for s in cb_status.values())

    subject_hours = {
        subj: round(mins / 60.0, 1) for subj, mins in today_minutes_by_subject.items()
    }
    total_study_hours = round(sum(today_minutes_by_subject.values()) / 60.0, 1)

    return JSONResponse({
        "date": today_str,
        "completion_pct": today_score or (min(100, int((completed_tasks / max(1, total_tasks or 8)) * 100))),
        "study": {
            "by_subject_hours": subject_hours,
            "total_hours": total_study_hours,
        },
        "tasks": {
            "completed": completed_tasks,
            "target": max(completed_tasks, total_tasks or 8),
        },
        "memory": {
            "total": total_memories,
            "avg_confidence": round(avg_conf, 2),
            "by_type": by_mem_type,
        },
        "ai_ops": {
            "providers_configured": sum(1 for p in pm.providers.values() if p.is_configured),
            "circuit_status": "healthy" if all_healthy else "degraded",
        },
    })

