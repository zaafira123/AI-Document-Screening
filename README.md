# AI-Based Fake Identity & Document Screening System (prototype)
Seva Innovation Challenge 2026. An explainable add-on layer for existing airport scanners and databases.
The officer always makes the final decision; the system never auto-rejects. **All data is fictional specimen data (state "Utopia", UTO).**

## Run on Windows
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m docscreen.samples.catalog
python server.py
```
Open http://localhost:5000. Login credentials are in `config/users.json` (shown on the login screen).
If RapidOCR fails to install, install Tesseract (UB-Mannheim build) and run `set DOCSCREEN_OCR=tesseract` before `python server.py`.
Optional stronger face matching: `python scripts/get_face_model.py`, then restart.

## Demo script (5 min)
1. Screening > load sample "genuine_passport" + live capture: LOW risk, proceed.
2. "tamper_dob_viz": DOB differs from MRZ, highlighted on the image. "tamper_photo_swap": photo noise + face mismatch.
3. "tamper_stamp_copy", "tamper_editor_meta": stamp clone and editing-software metadata.
4. "watchlist_person" / "stolen_document": simulated database hits.
5. Record a decision, open the case PDF report, then Audit trail > Verify chain, then Demo tamper > Verify (chain breaks) > Restore.
6. Rules page: edit severity points, Integration page: API example.

## Tests
`python run_tests.py` (unit) and `python scripts/evaluate.py` (21 scenarios).

## Honest limitations
- Tamper heuristics are calibrated on the synthetic sample set, not real documents; real-world thresholds need tuning with real data.
- The built-in face matcher is a demo baseline (eigenfaces). Use SFace/DeepFace for anything beyond demonstration.
- Watchlist is a SIMULATED adapter with fictional entries; real integration would use the Integration API.
- Risk scores assist the officer; they are not a legal determination.
