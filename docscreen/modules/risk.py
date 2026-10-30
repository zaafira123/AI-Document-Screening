"""Risk engine: finding severities -> points, per-module caps, level and recommended action.
The result is advice for the officer; nothing is ever rejected automatically."""
from __future__ import annotations

import json
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RULES_PATH = ROOT / "config" / "rules.json"
DEFAULT_PATH = ROOT / "config" / "rules.default.json"
_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_lock = threading.Lock()

ACTION_TEXT = {
    "proceed": "No significant indicators. Proceed with normal processing.",
    "refer": "Refer to a second officer for a closer look before clearing.",
    "secondary_inspection": "Recommend secondary inspection. The officer decides the outcome.",
}
MODULE_LABEL = {"quality": "Capture quality", "validation": "Document validation", "crosscheck": "Cross-document check",
                "tamper": "Tampering analysis", "metadata": "File metadata", "stamps": "Stamps", "face": "Face verification",
                "identity": "Identity linking", "watchlist": "Watchlist (simulated)"}


def load_rules() -> dict:
    with _lock:
        for p in (RULES_PATH, DEFAULT_PATH):
            try:
                return json.loads(p.read_text())
            except Exception:
                continue
    raise RuntimeError("no rules file")


def validate_rules(r: dict) -> dict:
    """Return a cleaned rules dict or raise ValueError."""
    base = json.loads(DEFAULT_PATH.read_text())
    out = json.loads(json.dumps(base))
    for sev in out["points"]:
        v = r.get("points", {}).get(sev, out["points"][sev])
        if not isinstance(v, (int, float)) or v < 0 or v > 100:
            raise ValueError(f"points.{sev} must be 0-100")
        out["points"][sev] = int(v)
    for m in out["caps"]:
        v = r.get("caps", {}).get(m, out["caps"][m])
        if not isinstance(v, (int, float)) or v < 0 or v > 100:
            raise ValueError(f"caps.{m} must be 0-100")
        out["caps"][m] = int(v)
    t = r.get("thresholds", out["thresholds"])
    if not (0 < t["medium"] < t["high"] <= 100):
        raise ValueError("thresholds must satisfy 0 < medium < high <= 100")
    out["thresholds"] = {"medium": int(t["medium"]), "high": int(t["high"])}
    out["critical_forces_high"] = bool(r.get("critical_forces_high", True))
    ns = r.get("name_similarity", out["name_similarity"])
    if not 0.5 <= ns <= 1:
        raise ValueError("name_similarity must be between 0.5 and 1")
    out["name_similarity"] = float(ns)
    out["expiry_warning_days"] = int(r.get("expiry_warning_days", out["expiry_warning_days"]))
    return out


def save_rules(r: dict) -> dict:
    clean = validate_rules(r)
    with _lock:
        RULES_PATH.write_text(json.dumps(clean, indent=2))
    return clean


def reset_rules() -> dict:
    with _lock:
        RULES_PATH.write_text(DEFAULT_PATH.read_text())
    return json.loads(DEFAULT_PATH.read_text())


def score(findings: list[dict], rules: dict | None = None) -> dict:
    """findings: each with severity, module, code, text (doc optional)."""
    rules = rules or load_rules()
    pts, caps = rules["points"], rules["caps"]
    per: dict[str, int] = {}
    for f in findings:
        per[f["module"]] = per.get(f["module"], 0) + pts.get(f["severity"], 0)
    capped = {m: min(v, caps.get(m, 100)) for m, v in per.items()}
    total = min(100, sum(capped.values()))
    worst = max((f["severity"] for f in findings), key=lambda s: _RANK[s], default="info")
    t = rules["thresholds"]
    level = "HIGH" if total >= t["high"] else "MEDIUM" if total >= t["medium"] else "LOW"
    if rules.get("critical_forces_high", True) and worst == "critical":
        level = "HIGH"
    action = rules["actions"][level]
    reasons = sorted([f for f in findings if _RANK[f["severity"]] >= 1], key=lambda f: (-_RANK[f["severity"]], f["module"], f["code"]))
    return {
        "score": int(total), "level": level, "action": action, "action_text": ACTION_TEXT[action],
        "contributions": [{"module": m, "label": MODULE_LABEL.get(m, m), "points": v, "raw": per[m], "cap": caps.get(m, 100)}
                          for m, v in sorted(capped.items(), key=lambda kv: -kv[1])],
        "reasons": [{"severity": f["severity"], "code": f["code"], "text": f["text"], "module": f["module"], "doc": f.get("doc"),
                     "bbox": f.get("bbox")} for f in reasons],
        "automatic_rejection": False,
        "thresholds": t,
    }
