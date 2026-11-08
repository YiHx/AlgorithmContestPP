"""SQLite 会话存储（M3 前置）：把一次会话的提交/榜单/事件/压力曲线落库，支持赛后复盘。

表：
    sessions   一次会话（平台、比赛、handle、预设、起止时间、meta/summary JSON）
    submissions 提交（去重：session_id + sid）
    snapshots  榜单快照
    events     反馈事件与闸门裁决
    pressure   压力曲线采样
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

from .platforms.base import ContestMeta, Standings, Submission

_LOCAL_FALLBACK = Path(__file__).resolve().parents[1] / ".local"


def default_db_path() -> Path:
    """默认 ~/.ojhc/ojhc.db；不可写时回退到 engine/.local/ojhc.db（沙箱/受限环境）。"""
    preferred = Path(os.environ.get("OJHC_HOME") or (Path.home() / ".ojhc")) / "ojhc.db"
    try:
        preferred.parent.mkdir(parents=True, exist_ok=True)
        return preferred
    except OSError:
        _LOCAL_FALLBACK.mkdir(parents=True, exist_ok=True)
        return _LOCAL_FALLBACK / "ojhc.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    contest_id TEXT NOT NULL,
    handle TEXT NOT NULL,
    preset TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL,
    meta_json TEXT NOT NULL,
    summary_json TEXT
);
CREATE TABLE IF NOT EXISTS submissions (
    session_id INTEGER NOT NULL,
    sid TEXT NOT NULL,
    rel_s REAL NOT NULL,
    verdict TEXT NOT NULL,
    problem TEXT NOT NULL,
    points REAL DEFAULT 0,
    UNIQUE(session_id, sid)
);
CREATE TABLE IF NOT EXISTS snapshots (
    session_id INTEGER NOT NULL,
    rel_s REAL NOT NULL,
    rank INTEGER, solved INTEGER, n_official INTEGER, frozen INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    session_id INTEGER NOT NULL,
    rel_s REAL NOT NULL,
    kind TEXT NOT NULL,
    s INTEGER NOT NULL,
    intensity INTEGER NOT NULL,
    duration_s REAL NOT NULL,
    would_fire INTEGER NOT NULL,
    allowed INTEGER DEFAULT 0,
    deny TEXT
);
CREATE TABLE IF NOT EXISTS pressure (
    session_id INTEGER NOT NULL,
    rel_s REAL NOT NULL,
    p REAL NOT NULL,
    level INTEGER NOT NULL,
    confidence TEXT
);
CREATE INDEX IF NOT EXISTS idx_sub_session ON submissions(session_id);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_pressure_session ON pressure(session_id);
"""


class Store:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        # 兼容旧库：补 allowed 列
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(events)")}
        if "allowed" not in cols:
            self.conn.execute("ALTER TABLE events ADD COLUMN allowed INTEGER DEFAULT 0")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -------------------------------------------------- 会话
    def create_session(self, platform: str, contest_id: str, handle: str,
                       preset: str, meta: ContestMeta) -> int:
        cur = self.conn.execute(
            "INSERT INTO sessions(platform, contest_id, handle, preset, started_at, meta_json)"
            " VALUES(?,?,?,?,?,?)",
            (platform, contest_id, handle, preset, time.time(),
             json.dumps(_meta_to_dict(meta), ensure_ascii=False)))
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_session(self, session_id: int, summary: dict) -> None:
        self.conn.execute(
            "UPDATE sessions SET ended_at=?, summary_json=? WHERE id=?",
            (time.time(), json.dumps(summary, ensure_ascii=False), session_id))
        self.conn.commit()

    def list_sessions(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, platform, contest_id, handle, preset, started_at, ended_at, summary_json"
            " FROM sessions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for row in rows:
            summary = json.loads(row["summary_json"]) if row["summary_json"] else {}
            out.append({
                "id": row["id"], "platform": row["platform"],
                "contest_id": row["contest_id"], "handle": row["handle"],
                "preset": row["preset"], "started_at": row["started_at"],
                "ended_at": row["ended_at"],
                "would_fire": summary.get("would_fire"),
                "pressure_max": summary.get("pressure_max"),
            })
        return out

    # -------------------------------------------------- 明细
    def add_submission(self, session_id: int, sub: Submission) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO submissions(session_id, sid, rel_s, verdict, problem, points)"
            " VALUES(?,?,?,?,?,?)",
            (session_id, sub.sid, sub.rel_s, sub.verdict, sub.problem, sub.points))

    def add_snapshot(self, session_id: int, st: Standings) -> None:
        self.conn.execute(
            "INSERT INTO snapshots(session_id, rel_s, rank, solved, n_official, frozen)"
            " VALUES(?,?,?,?,?,?)",
            (session_id, st.rel_s, st.rank, st.solved, st.n_official, int(st.frozen)))

    def add_event(self, session_id: int, rel_s: float, kind: str, s: int,
                  intensity: int, duration_s: float, would_fire: bool,
                  deny: list[str], allowed: bool = False) -> None:
        self.conn.execute(
            "INSERT INTO events(session_id, rel_s, kind, s, intensity, duration_s,"
            " would_fire, allowed, deny) VALUES(?,?,?,?,?,?,?,?,?)",
            (session_id, rel_s, kind, s, intensity, duration_s, int(would_fire),
             int(allowed), json.dumps(deny, ensure_ascii=False)))

    def add_pressure(self, session_id: int, rel_s: float, p: float, level: int,
                     confidence: str) -> None:
        self.conn.execute(
            "INSERT INTO pressure(session_id, rel_s, p, level, confidence) VALUES(?,?,?,?,?)",
            (session_id, rel_s, p, level, confidence))

    def commit(self) -> None:
        self.conn.commit()

    # -------------------------------------------------- 报告
    def report(self, session_id: int) -> dict:
        row = self.conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(f"没有 session {session_id}")
        subs = [dict(r) for r in self.conn.execute(
            "SELECT sid, rel_s, verdict, problem FROM submissions WHERE session_id=?"
            " ORDER BY rel_s", (session_id,))]
        events = [dict(r) for r in self.conn.execute(
            "SELECT rel_s, kind, s, intensity, duration_s, would_fire, allowed, deny"
            " FROM events WHERE session_id=? ORDER BY rel_s", (session_id,))]
        curve = [dict(r) for r in self.conn.execute(
            "SELECT rel_s, p, level FROM pressure WHERE session_id=?"
            " ORDER BY rel_s", (session_id,))]
        snapshots = [dict(r) for r in self.conn.execute(
            "SELECT rel_s, rank, solved, n_official, frozen FROM snapshots WHERE session_id=?"
            " ORDER BY rel_s", (session_id,))]
        for e in events:
            e["deny"] = json.loads(e["deny"]) if e["deny"] else []
        return {
            "session": dict(row),
            "meta": json.loads(row["meta_json"]),
            "summary": json.loads(row["summary_json"]) if row["summary_json"] else {},
            "submissions": subs,
            "events": events,
            "pressure": curve,
            "snapshots": snapshots,
        }


def _meta_to_dict(meta: ContestMeta) -> dict:
    return {
        "platform": meta.platform,
        "contest_id": meta.contest_id,
        "name": meta.name,
        "duration_s": meta.duration_s,
        "problems": [{"index": p.index, "rating": p.rating} for p in meta.problems],
        "start_ts": meta.start_ts,
        "frozen_from_s": meta.frozen_from_s,
        "url": meta.url,
    }
