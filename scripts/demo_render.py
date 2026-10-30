"""Render the specimen catalog into data/samples/ and check that the OCR engine can read every MRZ.

    python scripts/demo_render.py [--ref 2026-10-18]
"""
import argparse
import sys
import time
from datetime import date
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from docscreen.modules import ocr  # noqa: E402
from docscreen.samples.catalog import build_catalog  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--ref", default=None)
a = ap.parse_args()
ref = date.fromisoformat(a.ref) if a.ref else date.today()
out = ROOT / "data" / "samples"
manifest = build_catalog(out, ref)
info = ocr.engine_info()
print(f"reference date {ref}  |  {len(manifest)} samples in {out}")
print(f"OCR engine: {info['name']} ({info['version']})  errors: {info['errors']}\n")
good = total = 0
for s in manifest:
    for d in s["docs"]:
        img = cv2.imread(str(out / d["file"]))
        t = time.time()
        boxes = ocr.read_text(img)
        r = ocr.read_mrz(img, boxes, ref)
        dt = time.time() - t
        total += 1
        if r["found"]:
            ch = r["parsed"].get("checks", [])
            ok = sum(c["ok"] for c in ch)
            bad = [c["name"] for c in ch if not c["ok"]]
            good += bool(r["parsed"].get("ok"))
            print(f"{s['id']:<24}{d['role']:<16}{r['engine']:<10} fmt={str(r['parsed'].get('format')):<6} checks {ok}/{len(ch)}"
                  f"{'  FAILED: ' + ','.join(bad) if bad else ''}  [{dt:.1f}s]")
        else:
            print(f"{s['id']:<24}{d['role']:<16}{r['engine']:<10} MRZ not found  [{dt:.1f}s]")
print(f"\n{good}/{total} documents have a fully valid MRZ (tampered samples are expected to fail some checks)")
