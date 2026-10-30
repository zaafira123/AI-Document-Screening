"""Watchlist screening behind an adapter interface.

In production the adapter calls the border agency's alert list and the lost/stolen document
registry (for example an Interpol SLTD connection).  The prototype ships one SIMULATED adapter
backed by SQLite and filled with fictional entries - every hit is labelled SIMULATED.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from difflib import SequenceMatcher

from . import mrz as M
from .result import finding, result


def name_key(s: str) -> str:
    return " ".join(sorted(M.normalise_name(s).split()))


def name_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, name_key(a), name_key(b)).ratio()


class WatchlistAdapter(ABC):
    name = "adapter"
    simulated = False

    @abstractmethod
    def status(self) -> dict: ...

    @abstractmethod
    def search_person(self, name: str, dob: str | None) -> list[dict]: ...

    @abstractmethod
    def search_document(self, number: str) -> list[dict]: ...


class SimulatedWatchlist(WatchlistAdapter):
    name = "Simulated national alert list + lost/stolen document registry"
    simulated = True

    def __init__(self, db, min_name_similarity: float = 0.88):
        self.db = db
        self.min_sim = min_name_similarity
        self._seed()

    def _seed(self):
        from docscreen.samples.seed_data import WATCHLIST_DOCUMENTS, WATCHLIST_PERSONS

        if not self.db.q("SELECT 1 FROM watch_persons LIMIT 1"):
            for p in WATCHLIST_PERSONS:
                self.db.x("INSERT OR IGNORE INTO watch_persons VALUES(?,?,?,?,?)", (p["ref"], p["name"], p["dob"], p["category"], p["note"]))
            for d in WATCHLIST_DOCUMENTS:
                self.db.x("INSERT OR IGNORE INTO watch_docs VALUES(?,?,?,?,?)", (d["ref"], d["number"], d["doc_type"], d["status"], d["note"]))

    def status(self) -> dict:
        return {"adapter": self.name, "simulated": True, "connected": True,
                "persons": self.db.q("SELECT COUNT(*) n FROM watch_persons")[0]["n"],
                "documents": self.db.q("SELECT COUNT(*) n FROM watch_docs")[0]["n"], "mode": "local SQLite (prototype)"}

    def search_person(self, name: str, dob: str | None) -> list[dict]:
        hits = []
        for p in self.db.q("SELECT * FROM watch_persons"):
            sim = name_similarity(name, p["name"])
            dob_match = bool(dob and p["dob"] and dob == p["dob"])
            if (sim >= self.min_sim and dob_match) or sim >= 0.95 or (dob_match and sim >= 0.75):
                hits.append({"ref": p["ref"], "name": p["name"], "dob": p["dob"], "category": p["category"], "note": p["note"],
                             "name_similarity": round(sim, 3), "dob_match": dob_match, "simulated": True,
                             "strength": "strong" if (sim >= self.min_sim and dob_match) else "possible"})
        return sorted(hits, key=lambda h: -h["name_similarity"])

    def search_document(self, number: str) -> list[dict]:
        n = "".join(c for c in (number or "").upper() if c.isalnum())
        if not n:
            return []
        return [dict(d, simulated=True) for d in self.db.q("SELECT * FROM watch_docs")
                if "".join(c for c in d["number"].upper() if c.isalnum()) == n]


def screen(adapter: WatchlistAdapter, people: list[dict]) -> dict:
    """people: [{name, dob, number, doc_type}]"""
    fs, hits = [], []
    seen = set()
    for p in people:
        if p.get("name") and (p["name"], p.get("dob")) not in seen:
            seen.add((p["name"], p.get("dob")))
            for h in adapter.search_person(p["name"], p.get("dob")):
                hits.append(dict(h, kind="person"))
                grave = h["category"].upper().startswith(("WANTED", "ENTRY BAN"))
                if h["strength"] == "strong":
                    fs.append(finding("critical" if grave else "high", "WL_PERSON",
                                      f"[SIMULATED] Name ({h['name_similarity']:.0%} similar) and date of birth match watchlist entry "
                                      f"{h['ref']} - category {h['category']}. Verify identity before any action."))
                else:
                    fs.append(finding("medium", "WL_PERSON_POSSIBLE",
                                      f"[SIMULATED] Possible match to watchlist entry {h['ref']} ({h['category']}): "
                                      f"name {h['name_similarity']:.0%} similar, date of birth {'matches' if h['dob_match'] else 'differs'}."))
        if p.get("number"):
            for h in adapter.search_document(p["number"]):
                hits.append(dict(h, kind="document"))
                fs.append(finding("critical", "WL_DOCUMENT",
                                  f"[SIMULATED] Document number {p['number']} is reported {h['status']} in the lost/stolen registry ({h['ref']})."))
    return result(fs, {"adapter": adapter.status(), "hits": hits, "checked": len(seen)})
