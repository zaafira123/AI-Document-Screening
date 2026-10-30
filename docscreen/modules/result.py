"""Shared result shape used by every analysis module.

    {"status": "ok" | "warn" | "fail" | "skipped",
     "findings": [{"severity", "code", "text", "bbox": [x, y, w, h] | None}],
     "details": {...}}

Severities (lowest to highest): info, low, medium, high, critical.
"""
from __future__ import annotations

SEVERITIES = ("info", "low", "medium", "high", "critical")
_RANK = {s: i for i, s in enumerate(SEVERITIES)}


def finding(severity: str, code: str, text: str, bbox=None, **extra) -> dict:
    if severity not in _RANK:
        raise ValueError(f"unknown severity {severity!r}")
    f = {"severity": severity, "code": code, "text": text,
         "bbox": [int(v) for v in bbox] if bbox is not None else None}
    f.update(extra)
    return f


def status_from(findings: list[dict]) -> str:
    worst = max((_RANK[f["severity"]] for f in findings), default=0)
    if worst >= _RANK["high"]:
        return "fail"
    if worst >= _RANK["low"]:
        return "warn"
    return "ok"


def result(findings: list[dict] | None = None, details: dict | None = None, status: str | None = None) -> dict:
    findings = findings or []
    return {"status": status or status_from(findings), "findings": findings, "details": details or {}}


def skipped(reason: str) -> dict:
    return {"status": "skipped", "findings": [], "details": {"reason": reason}}


def worst(findings: list[dict]) -> str:
    return max((f["severity"] for f in findings), key=lambda s: _RANK[s], default="info")
