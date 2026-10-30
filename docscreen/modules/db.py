"""SQLite storage: cases, hash-chained audit log, identity records, simulated watchlist tables."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "storage" / "docscreen.db"
GENESIS = "0" * 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases(
  id TEXT PRIMARY KEY, created_at TEXT, reference_date TEXT, checkpoint TEXT, booth TEXT, screened_by TEXT,
  traveller TEXT, doc_summary TEXT, risk_score INTEGER, risk_level TEXT, action TEXT, processing_ms INTEGER,
  result_json TEXT, decision TEXT, decision_note TEXT, decided_by TEXT, decided_at TEXT);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY, ts TEXT, actor TEXT, role TEXT, action TEXT, case_id TEXT, details TEXT, prev_hash TEXT, hash TEXT);
CREATE TABLE IF NOT EXISTS identities(
  id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, name_norm TEXT, dob TEXT, doc_number TEXT, doc_type TEXT,
  embedding BLOB, engine TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS watch_persons(ref TEXT PRIMARY KEY, name TEXT, dob TEXT, category TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS watch_docs(ref TEXT PRIMARY KEY, number TEXT, doc_type TEXT, status TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS case_seq(day TEXT PRIMARY KEY, n INTEGER);
"""


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def chain_hash(rec: dict) -> str:
    keys = ("id", "ts", "actor", "role", "action", "case_id", "details", "prev_hash")
    return hashlib.sha256(canonical({k: rec[k] for k in keys}).encode("utf8")).hexdigest()


class DB:
    def __init__(self, path: Path | str = DB_PATH):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    # -- generic -------------------------------------------------------------
    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def x(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    # -- audit log (SHA-256 hash chain) ----------------------------------------
    def audit(self, actor: str, role: str, action: str, case_id: str | None = None, details: dict | None = None) -> dict:
        with self.lock:
            last = self.conn.execute("SELECT id, hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
            rec = {"id": (last["id"] + 1) if last else 1, "ts": now(), "actor": actor, "role": role, "action": action,
                   "case_id": case_id, "details": canonical(details or {}), "prev_hash": last["hash"] if last else GENESIS}
            rec["hash"] = chain_hash(rec)
            self.conn.execute("INSERT INTO audit(id,ts,actor,role,action,case_id,details,prev_hash,hash) VALUES(?,?,?,?,?,?,?,?,?)",
                              tuple(rec[k] for k in ("id", "ts", "actor", "role", "action", "case_id", "details", "prev_hash", "hash")))
            self.conn.commit()
            return rec

    def audit_rows(self, limit: int = 200, case_id: str | None = None):
        if case_id:
            return self.q("SELECT * FROM audit WHERE case_id=? ORDER BY id DESC LIMIT ?", (case_id, limit))
        return self.q("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))

    def verify_audit(self) -> dict:
        rows = self.q("SELECT * FROM audit ORDER BY id ASC")
        prev = GENESIS
        for i, r in enumerate(rows):
            if r["prev_hash"] != prev:
                return {"ok": False, "count": len(rows), "first_bad_id": r["id"], "reason": "chain link broken (a record was removed or reordered)"}
            if chain_hash(r) != r["hash"]:
                return {"ok": False, "count": len(rows), "first_bad_id": r["id"], "reason": "record content does not match its hash (edited)"}
            prev = r["hash"]
        return {"ok": True, "count": len(rows), "head": prev, "genesis": GENESIS}

    # -- cases ---------------------------------------------------------------------
    def next_case_id(self, day: str) -> str:
        with self.lock:
            row = self.conn.execute("SELECT n FROM case_seq WHERE day=?", (day,)).fetchone()
            n = (row["n"] if row else 0) + 1
            self.conn.execute("INSERT OR REPLACE INTO case_seq(day,n) VALUES(?,?)", (day, n))
            self.conn.commit()
        return f"C-{day.replace('-', '')}-{n:04d}"

    def save_case(self, c: dict):
        self.x("INSERT OR REPLACE INTO cases(id,created_at,reference_date,checkpoint,booth,screened_by,traveller,doc_summary,risk_score,"
               "risk_level,action,processing_ms,result_json,decision,decision_note,decided_by,decided_at) "
               "VALUES(:id,:created_at,:reference_date,:checkpoint,:booth,:screened_by,:traveller,:doc_summary,:risk_score,:risk_level,"
               ":action,:processing_ms,:result_json,:decision,:decision_note,:decided_by,:decided_at)", c)

    def cases(self, limit=200):
        return self.q("SELECT id,created_at,reference_date,checkpoint,booth,screened_by,traveller,doc_summary,risk_score,risk_level,action,"
                      "processing_ms,decision,decided_by,decided_at FROM cases ORDER BY created_at DESC, id DESC LIMIT ?", (limit,))

    def case(self, cid: str):
        r = self.q("SELECT * FROM cases WHERE id=?", (cid,))
        return r[0] if r else None

    def decide(self, cid: str, decision: str, note: str, by: str):
        self.x("UPDATE cases SET decision=?, decision_note=?, decided_by=?, decided_at=? WHERE id=?", (decision, note, by, now(), cid))

    # -- maintenance -----------------------------------------------------------------
    def reset_demo(self):
        """Clear cases, identities and the audit log (a fresh demo). The simulated watchlist is kept."""
        with self.lock:
            for t in ("cases", "audit", "identities", "case_seq"):
                self.conn.execute(f"DELETE FROM {t}")
            self.conn.commit()

    def close(self):
        with self.lock:
            if self.conn:
                try:
                    self.conn.close()
                except Exception:
                    pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

