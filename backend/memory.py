"""Persistent long-term memory — SQLite-backed storage for Soft-Mianmian.

Three tables:
- user_facts: things the user told us ("我叫小北", "我怕冷")
- conversation_summaries: compressed old chat turns
- environment_notes: per-period environment snapshots
"""
import json
import os
import sqlite3
import threading
import time

# DB file lives next to app.py in the project root
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mianmian_memory.db")

_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact TEXT NOT NULL,
    source TEXT DEFAULT '',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    summary TEXT NOT NULL,
    start_time TEXT DEFAULT '',
    end_time TEXT DEFAULT '',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS environment_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time_period TEXT DEFAULT '',
    summary TEXT NOT NULL,
    avg_data TEXT DEFAULT '{}',
    created_at REAL NOT NULL
);
"""


def _get_conn():
    """Return a thread-local connection. Auto-creates tables on first open."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _now():
    return time.time()


# ── User Facts ──


def remember_user_fact(fact: str, source: str = "conversation") -> int:
    """Store a fact about the user. Returns the new row id."""
    fact = fact.strip()
    if not fact:
        return -1
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            "INSERT INTO user_facts (fact, source, created_at) VALUES (?, ?, ?)",
            (fact, source, _now()),
        )
        conn.commit()
        return cur.lastrowid


def get_user_facts(limit: int = 20, query: str = "") -> list[dict]:
    """Retrieve user facts, newest first. If query is given, filter by LIKE."""
    with _lock:
        conn = _get_conn()
        if query:
            rows = conn.execute(
                "SELECT id, fact, source, created_at FROM user_facts "
                "WHERE fact LIKE ? ORDER BY created_at DESC LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, fact, source, created_at FROM user_facts "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [_row_to_dict(r, ("id", "fact", "source", "created_at")) for r in rows]


def delete_user_fact(fact_id: int) -> bool:
    """Remove a fact by id. Returns True if anything was deleted."""
    with _lock:
        conn = _get_conn()
        cur = conn.execute("DELETE FROM user_facts WHERE id = ?", (fact_id,))
        conn.commit()
        return cur.rowcount > 0


def count_user_facts() -> int:
    """Return total number of stored facts."""
    with _lock:
        conn = _get_conn()
        return conn.execute("SELECT COUNT(*) FROM user_facts").fetchone()[0]


# ── Conversation Summaries ──


def add_conversation_summary(summary: str, start_time: str = "", end_time: str = "") -> int:
    """Store a compressed summary of a conversation session."""
    summary = summary.strip()
    if not summary:
        return -1
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            "INSERT INTO conversation_summaries (summary, start_time, end_time, created_at) "
            "VALUES (?, ?, ?, ?)",
            (summary, start_time, end_time, _now()),
        )
        conn.commit()
        return cur.lastrowid


def get_recent_summaries(limit: int = 5) -> list[dict]:
    """Get the most recent conversation summaries."""
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT id, summary, start_time, end_time, created_at "
            "FROM conversation_summaries ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_dict(r, ("id", "summary", "start_time", "end_time", "created_at")) for r in rows]


# ── Environment Notes ──


def add_environment_note(time_period: str, summary: str, avg_data: dict | None = None) -> int:
    """Store a summary of environment conditions over a time period."""
    summary = summary.strip()
    if not summary:
        return -1
    avg_json = json.dumps(avg_data or {}, ensure_ascii=False)
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            "INSERT INTO environment_notes (time_period, summary, avg_data, created_at) "
            "VALUES (?, ?, ?, ?)",
            (time_period, summary, avg_json, _now()),
        )
        conn.commit()
        return cur.lastrowid


def get_environment_notes(limit: int = 5) -> list[dict]:
    """Get recent environment notes, newest first."""
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT id, time_period, summary, avg_data, created_at "
            "FROM environment_notes ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_dict(r, ("id", "time_period", "summary", "avg_data", "created_at")) for r in rows]


# ── Helpers ──


def _row_to_dict(row, keys):
    d = dict(zip(keys, row))
    if "avg_data" in d and isinstance(d["avg_data"], str):
        try:
            d["avg_data"] = json.loads(d["avg_data"])
        except (json.JSONDecodeError, TypeError):
            pass
    return d


def get_all_user_facts_as_strings(limit: int = 10) -> list[str]:
    """Return user facts as a simple list of strings (for prompt building)."""
    facts = get_user_facts(limit=limit)
    return [f["fact"] for f in facts]
