"""SQLite persistence: sent reports (duplicate prevention), FVG state, session state."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from gold_bot.analysis.fvg import FVG, FVGDirection
from gold_bot.models import Candle, Timeframe

SCHEMA = """
CREATE TABLE IF NOT EXISTS sent_reports (
    key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fvgs (
    key TEXT PRIMARY KEY,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,
    lower REAL NOT NULL,
    upper REAL NOT NULL,
    candle1_time TEXT NOT NULL,
    candle3_time TEXT NOT NULL,
    extreme REAL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS fvgs_tf_status ON fvgs (timeframe, status);
CREATE TABLE IF NOT EXISTS session_state (
    session_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Storage:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()

    def close(self) -> None:
        self._conn.close()

    # ----- duplicate prevention ------------------------------------------ #
    def report_status(self, key: str) -> str | None:
        row = self._conn.execute("SELECT status FROM sent_reports WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def claim_report(self, key: str, kind: str) -> bool:
        """Atomically claim a report key. False if it was already sent/claimed."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO sent_reports (key, kind, status, updated_at) VALUES (?, ?, 'sending', ?)",
                (key, kind, _now()),
            )
            return cur.rowcount == 1

    def finish_report(self, key: str, status: str, detail: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sent_reports SET status = ?, detail = ?, updated_at = ? WHERE key = ?",
                (status, detail, _now(), key),
            )

    def release_report(self, key: str) -> None:
        """Undo a claim after a failed send so a later poll can retry."""
        with self._lock:
            self._conn.execute("DELETE FROM sent_reports WHERE key = ? AND status = 'sending'", (key,))

    # ----- FVG persistence ----------------------------------------------- #
    def merge_fvgs(self, timeframe: Timeframe, fresh: list[FVG], action: list[Candle]) -> list[FVG]:
        """Upsert freshly detected gaps and advance stored ones with new price action.

        Returns every gap for ``timeframe`` (including filled ones, which are kept
        with status Filled for history but filtered out of reports).
        """
        with self._lock:
            stored = {
                row[0]: FVG(
                    timeframe=Timeframe(row[1]),
                    direction=FVGDirection(row[2]),
                    lower=row[3],
                    upper=row[4],
                    candle1_time=datetime.fromisoformat(row[5]),
                    candle3_time=datetime.fromisoformat(row[6]),
                    extreme=row[7],
                )
                for row in self._conn.execute(
                    "SELECT key, timeframe, direction, lower, upper, candle1_time, candle3_time, extreme "
                    "FROM fvgs WHERE timeframe = ? AND status != 'Filled'",
                    (timeframe.value,),
                )
            }
            result: dict[str, FVG] = {}
            for gap in fresh:
                old = stored.pop(gap.key, None)
                if old is not None and old.extreme is not None:
                    gap = _combine_extreme(gap, old)
                result[gap.key] = gap
            for key, gap in stored.items():  # older than the fetched window
                result[key] = gap.with_price_action(action)
            for gap in result.values():
                self._conn.execute(
                    "INSERT INTO fvgs (key, timeframe, direction, lower, upper, candle1_time, candle3_time, extreme, "
                    "status, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET extreme = excluded.extreme, status = excluded.status, "
                    "updated_at = excluded.updated_at",
                    (
                        gap.key,
                        gap.timeframe.value,
                        gap.direction.value,
                        gap.lower,
                        gap.upper,
                        gap.candle1_time.isoformat(),
                        gap.candle3_time.isoformat(),
                        gap.extreme,
                        gap.status.value,
                        _now(),
                    ),
                )
            return list(result.values())

    def fvg_count(self, status: str | None = None) -> int:
        if status:
            return self._conn.execute("SELECT COUNT(*) FROM fvgs WHERE status = ?", (status,)).fetchone()[0]
        return self._conn.execute("SELECT COUNT(*) FROM fvgs").fetchone()[0]

    # ----- session state -------------------------------------------------- #
    def save_session_state(self, session_id: str, data: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO session_state (session_id, data, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
                (session_id, json.dumps(data), _now()),
            )

    def load_session_state(self, session_id: str) -> dict | None:
        row = self._conn.execute("SELECT data FROM session_state WHERE session_id = ?", (session_id,)).fetchone()
        return json.loads(row[0]) if row else None


def _combine_extreme(fresh: FVG, old: FVG) -> FVG:
    """Keep the deepest penetration seen (extremes only ever move further in)."""
    from dataclasses import replace

    if fresh.extreme is None:
        return replace(fresh, extreme=old.extreme)
    if fresh.direction is FVGDirection.BULLISH:
        return replace(fresh, extreme=min(fresh.extreme, old.extreme))
    return replace(fresh, extreme=max(fresh.extreme, old.extreme))
