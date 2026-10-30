"""Unit and regression tests for field extraction, tamper detection, MRZ validation, and database reliability."""
from datetime import date
import numpy as np

from docscreen.modules import fields, mrz, validation, tamper
from docscreen.modules.db import DB


def test_bilingual_label_matching():
    # French labels
    assert fields.label_of("Nom") == "surname"
    assert fields.label_of("Prénoms") == "given"
    assert fields.label_of("Prenoms") == "given"
    assert fields.label_of("Date de naissance") == "dob"
    assert fields.label_of("Lieu de naissance") == "pob"
    assert fields.label_of("Nationalité") == "nationality"
    assert fields.label_of("Date de délivrance") == "issue"
    assert fields.label_of("Date d'expiration") == "expiry"
    assert fields.label_of("Valable du") == "issue"
    assert fields.label_of("Jusqu'au") == "expiry"
    assert fields.label_of("Entrées") == "entries"
    assert fields.label_of("Durée du séjour") == "stay"
    assert fields.label_of("Type de permis") == "permit_type"

    # Irregular spacing and punctuation from OCR
    assert fields.label_of("PassportNo./Ndupasseport") == "passport_no"
    assert fields.label_of("Dateofissue/Datededelivrance") == "issue"
    assert fields.label_of("Dateofexpiry/Dated'expiration") == "expiry"
    assert fields.label_of("Sex/Sexe") == "sex"
    assert fields.label_of("Sex/ Sexe") == "sex"
    assert fields.label_of("Placeofbirth/Lieudenaissance") == "pob"


def test_labels_never_mistaken_for_values():
    # General document template labels must be recognized as labels
    assert fields.is_document_label("Code/Code")
    assert fields.is_document_label("Type/Type")
    assert fields.is_document_label("Authority/Autorite")
    assert fields.is_document_label("Holder's signature/Signaturedutitulaire")
    assert fields.is_document_label("Republic of Utopia")
    assert fields.is_document_label("Passport / Passeport")
    assert fields.is_document_label("IMMIGRATIONSERVICE")

    # Simulation of boxes where a label sits above another label
    # E.g. 'Sex/Sexe' at y=369, 'Dateofissue/...' at y=441, but NO value for sex
    boxes = [
        {"text": "Sex/Sexe", "x": 329, "y": 369, "w": 76, "h": 19, "conf": 90},
        {"text": "Dateofissue/Datededelivrance", "x": 330, "y": 441, "w": 221, "h": 16, "conf": 90},
        {"text": "19OCT2023", "x": 329, "y": 464, "w": 175, "h": 25, "conf": 85},
    ]
    extracted = fields.extract(boxes)
    # Sex should NOT have picked 'Dateofissue' as its value
    assert "sex" not in extracted or extracted["sex"].get("value") is None
    # Issue date should be correctly extracted
    assert "issue" in extracted
    assert extracted["issue"]["value"] == "2023-10-19"


def test_date_parsing_formats():
    # Various valid date formats
    assert fields.parse_date("12 AUG 1974") == date(1974, 8, 12)
    assert fields.parse_date("19OCT2023") == date(2023, 10, 19)
    assert fields.parse_date("19-OCT-2023") == date(2023, 10, 19)
    assert fields.parse_date("19.OCT.2023") == date(2023, 10, 19)
    assert fields.parse_date("19/OCT/2023") == date(2023, 10, 19)
    assert fields.parse_date("2026-10-18") == date(2026, 10, 18)
    assert fields.parse_date("18/10/2026") == date(2026, 10, 18)

    # French months
    assert fields.parse_date("12 AOU 1974") == date(1974, 8, 12)
    assert fields.parse_date("15 AVR 2025") == date(2025, 4, 15)
    assert fields.parse_date("01 MAI 2024") == date(2024, 5, 1)
    assert fields.parse_date("28 FEV 2023") == date(2023, 2, 28)

    # Invalid dates
    assert fields.parse_date("NOT A DATE") is None
    assert fields.parse_date("32 JAN 2020") is None


def test_name_equality_with_spacing():
    # Spacing differences from OCR concatenation or splitting
    assert validation._name_eq("ANNAMARIA", "ANNA MARIA")
    assert validation._name_eq("ANNA MARIA", "ANNAMARIA")
    assert validation._name_eq("TOMASJOSEF", "TOMAS JOSEF")
    assert validation._name_eq("DE LA CRUZ", "DELACRUZ")

    # Truncated long names
    assert validation._name_eq("VERYLONGNAMEWITHEXACTLYTHIRTYCHARS", "VERYLONGNAMEWITHEXACTLYTHIRTY")

    # Truly different names should not match (tampering detection preserved)
    assert not validation._name_eq("HARTMAN", "HARTMANN")
    assert not validation._name_eq("JOHN", "JAMES")
    assert not validation._name_eq("ERIKSSON", "ERIKSON")


def test_mrz_personal_number_check_digit():
    # When personal number position 43 is filler '<' (ICAO 9303 Part 4 unutilized/no check digit)
    res = mrz._chk("personal_number", "<<<<<<<<<<<<<<", "<", "Personal number")
    assert res["ok"] is True
    assert res["expected"] == "<"
    assert res["found"] == "<"

    # When personal number is present but position 43 is filler '<'
    res2 = mrz._chk("personal_number", "ZE184226B<<<<<", "<", "Personal number")
    assert res2["ok"] is True
    assert res2["expected"] == "<"

    # When position 43 has a numeric check digit, it must be verified
    expected_cd = mrz.check_digit("ZE184226B<<<<<")
    res3 = mrz._chk("personal_number", "ZE184226B<<<<<", expected_cd, "Personal number")
    assert res3["ok"] is True

    # Bad check digit must fail
    bad_cd = str((int(expected_cd) + 1) % 10)
    res4 = mrz._chk("personal_number", "ZE184226B<<<<<", bad_cd, "Personal number")
    assert res4["ok"] is False


def test_tamper_sharpness_discrimination():
    # Static printed label with small (~3%) sharpness difference should not trigger finding
    dummy_img = np.full((300, 300, 3), 200, dtype=np.uint8)
    boxes = [
        {"text": "Code/Code", "x": 20, "y": 20, "w": 80, "h": 20, "conf": 90},
        {"text": "UTO", "x": 20, "y": 50, "w": 60, "h": 25, "conf": 80},
    ]
    fields_dict = {"nationality": {"value": "UTO", "bbox": [20, 50, 60, 25]}}
    findings = tamper.text_consistency(dummy_img, boxes, fields_dict)
    assert len(findings) == 0, findings


def test_mrz_fallback_distinction():
    # When sex is missing from visual boxes, extract_robust fills it from MRZ with metadata
    mrz_parsed = {
        "surname": "ERIKSSON",
        "given_names": "ANNA MARIA",
        "doc_number": "UT482917",
        "dob_iso": "1974-08-12",
        "expiry_iso": "2033-10-19",
        "sex": "F",
        "nationality": "UTO",
    }
    img = np.full((100, 100, 3), 200, dtype=np.uint8)
    # Boxes without sex
    boxes = [
        {"text": "Surname/Nom", "x": 10, "y": 10, "w": 80, "h": 15, "conf": 90},
        {"text": "ERIKSSON", "x": 10, "y": 30, "w": 80, "h": 20, "conf": 90},
    ]
    f = fields.extract_robust(img, boxes, "passport", mrz_parsed=mrz_parsed)
    assert "sex" in f
    assert f["sex"]["value"] == "F"
    assert f["sex"].get("source") == "mrz"
    assert f["sex"].get("mrz_fallback") is True

    # Validate does not report VAL_FIELD_MISSING for sex
    res = validation.validate("passport", {"parsed": mrz_parsed, "found": True}, f, date(2026, 10, 18))
    missing = [c for c in res["findings"] if c["code"] == "VAL_FIELD_MISSING"]
    assert len(missing) == 0, missing


def test_db_context_manager_and_close():
    import tempfile, os
    tmp = os.path.join(tempfile.gettempdir(), "test_db_ctx.db")
    try:
        with DB(tmp) as db:
            db.audit("test", "admin", "TEST_ACTION", None, {"key": "val"})
            assert len(db.audit_rows()) == 1
        assert db.conn is not None
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass



def test_presentation_attack_detection_liveness():
    from docscreen.modules import face
    import cv2
    p = "data/samples/genuine_passport/live.jpg"
    img = cv2.imread(p)
    assert img is not None
    res = face.check_liveness(img)
    assert res["liveness_score"] >= 0.70
    assert res["decision"] == "live"
    assert len(res["reasons"]) == 0

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    flat_print = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    res_spoof = face.check_liveness(flat_print)
    assert res_spoof["liveness_score"] < 0.70
    assert any("chrominance" in r.lower() for r in res_spoof["reasons"])


def test_document_deskew_and_guidance():
    from docscreen.modules import quality
    import cv2
    img = cv2.imread("data/samples/genuine_passport/passport.jpg")
    assert img is not None

    res = quality.analyse(img)
    assert "guidance" in res["details"]
    assert "skew_angle" in res["details"]
    assert abs(res["details"]["skew_angle"]) < 2.0

    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), 6.0, 1.0)
    tilted = cv2.warpAffine(img, M, (w, h), borderMode=cv2.BORDER_REPLICATE)
    skew = quality.estimate_skew(tilted)
    assert abs(skew) > 3.0
    corrected = quality.deskew(tilted, skew)
    assert corrected.shape == img.shape


def test_barcode_detector_integration():
    from docscreen.modules import fields
    import cv2
    img = cv2.imread("data/samples/genuine_passport/passport.jpg")
    assert img is not None
    bc = fields.read_barcodes(img)
    assert isinstance(bc, list)
