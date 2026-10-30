"""Scenario check: runs every sample through the pipeline (temp DB) and compares with the expected risk level.
Usage: python scripts/evaluate.py [--verbose] [sample_id ...]
"""
import argparse
import json
import os
import sys
import tempfile
import time
import uuid
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from docscreen.modules.db import DB
from docscreen.modules.pipeline import Screener

root = Path("data/samples")
manifest_path = root / "manifest.json"

if not manifest_path.exists():
    sys.exit(
        f"Error: Manifest file '{manifest_path}' not found.\n"
        "Please generate the sample catalog first by running:\n"
        "  python -m docscreen.samples.catalog"
    )

try:
    man = json.load(open(manifest_path, encoding="utf-8"))["samples"]
except Exception as e:
    sys.exit(f"Error reading manifest '{manifest_path}': {e}")

byid = {s["id"]: s for s in man}


def run(S, s, ref):
    docs = [{"name": d["file"], "bytes": (root / d["file"]).read_bytes(), "role": d["role"]} for d in s["docs"]]
    live = (root / s["live"]).read_bytes() if s.get("live") else None
    return S.screen(docs, live, ref, {"officer": "OFC-1042"})


def main():
    parser = argparse.ArgumentParser(description="Evaluate document screening scenarios")
    parser.add_argument("samples", nargs="*", help="Specific sample ID(s) to evaluate")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print verbose details for each scenario")
    args = parser.parse_args()

    want = set(args.samples)
    ok = 0
    n = 0
    failures = []

    print(f"{'STATUS':<6} {'SCENARIO ID':<26} {'ACTUAL':<11} {'EXPECTED':<12} {'TIME':<6} {'FINDINGS'}", flush=True)
    print("-" * 90, flush=True)

    for s in man:
        if want and s["id"] not in want:
            continue

        temp_db_path = os.path.join(tempfile.gettempdir(), f"docscreen_eval_{uuid.uuid4().hex[:8]}.db")
        db = DB(temp_db_path)
        try:
            S = Screener(db)
            ref = date(2026, 10, 18)
            for r in s.get("requires", []):
                if r in byid:
                    run(S, byid[r], ref)

            t0 = time.time()
            res = run(S, s, ref)
            elapsed = time.time() - t0
            rk = res["risk"]

            if args.verbose:
                print(f"\n--- VERBOSE: {s['id']} ---", flush=True)
                for d in res["documents"]:
                    print(f"Document: {d.get('filename')}", flush=True)
                    print(f"Type: {d.get('doc_type')}", flush=True)
                    print(f"Extracted fields: { {k: v.get('value') for k, v in d.get('fields', {}).items()} }", flush=True)
                    print(f"Validation details: {d.get('validation', {}).get('details')}", flush=True)
                    print(f"Tamper findings: {d.get('tamper', {}).get('findings', [])}", flush=True)

            codes = [f["code"] for f in res["findings"] if f.get("severity") != "info"]
            lvl_ok = rk["level"] in s["expect"]["levels"]
            missing_codes = [e for e in s["expect"]["codes"] if not any(c.startswith(e) for c in codes)]
            code_ok = len(missing_codes) == 0

            n += 1
            passed = lvl_ok and code_ok
            if passed:
                ok += 1
            else:
                failures.append({
                    "id": s["id"],
                    "actual_level": rk["level"],
                    "expected_levels": s["expect"]["levels"],
                    "actual_score": rk["score"],
                    "missing_codes": missing_codes,
                    "findings": codes,
                })

            status_str = "OK " if passed else "FAIL"
            act_str = f"{rk['level']}({rk['score']})"
            exp_str = ",".join(s["expect"]["levels"])
            print(f"{status_str:<6} {s['id']:<26} {act_str:<11} {exp_str:<12} {elapsed:>4.1f}s {codes}", flush=True)

        finally:
            db.close()
            if os.path.exists(temp_db_path):
                try:
                    os.remove(temp_db_path)
                except Exception:
                    pass

    print("-" * 90, flush=True)
    print(f"Result: {ok}/{n} scenarios passed as expected.", flush=True)
    if failures:
        print("\nFailures summary:", flush=True)
        for f in failures:
            reasons = []
            if f["actual_level"] not in f["expected_levels"]:
                reasons.append(f"risk level {f['actual_level']} not in expected {f['expected_levels']}")
            if f["missing_codes"]:
                reasons.append(f"missing expected code prefixes: {f['missing_codes']}")
            print(f" - {f['id']}: " + "; ".join(reasons), flush=True)
    return 0 if ok == n else 1


if __name__ == "__main__":
    sys.exit(main())

