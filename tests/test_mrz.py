from datetime import date

from docscreen.modules import mrz

# ICAO Doc 9303 Part 4 specimen
L1 = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<"
L2 = "L898902C36UTO7408122F1204159ZE184226B<<<<<10"
TODAY = date(2026, 10, 17)


def test_check_digit_specimen():
    assert mrz.check_digit("L898902C3") == "6"
    assert mrz.check_digit("740812") == "2"
    assert mrz.check_digit("120415") == "9"


def test_parse_icao_specimen():
    p = mrz.parse_mrz([L1, L2], TODAY)
    assert p["format"] == "TD3"
    assert p["surname"] == "ERIKSSON"
    assert p["given_names"] == "ANNA MARIA"
    assert p["doc_number"] == "L898902C3"
    assert p["nationality"] == "UTO"
    assert p["dob_iso"] == "1974-08-12"
    assert p["expiry_iso"] == "2012-04-15"
    assert p["ok"], p["checks"]


def test_detects_tampered_dob():
    bad = L2[:13] + "740813" + L2[19:]  # changed day, check digit not updated
    p = mrz.parse_mrz([L1, bad], TODAY)
    assert not p["ok"]
    failed = [c["name"] for c in p["checks"] if not c["ok"]]
    assert "dob" in failed and "composite" in failed


def test_builder_roundtrip_td3():
    lines = mrz.build_td3(issuer="UTO", surname="Okafor", given="Chidi Emeka", number="UT4821907",
                          nationality="UTO", dob=date(1988, 3, 14), sex="M", expiry=date(2033, 5, 2))
    assert [len(l) for l in lines] == [44, 44]
    assert mrz.parse_mrz(lines, TODAY)["ok"]


def test_builder_roundtrip_td1_and_visa():
    td1 = mrz.build_td1(issuer="UTO", surname="Ivanova", given="Mira", number="ID7712093",
                        nationality="UTO", dob=date(1995, 1, 9), sex="F", expiry=date(2031, 1, 8))
    assert [len(l) for l in td1] == [30, 30, 30]
    assert mrz.parse_mrz(td1, TODAY)["ok"]
    visa = mrz.build_mrva(issuer="UTO", surname="Okafor", given="Chidi", number="V0012345",
                          nationality="UTO", dob=date(1988, 3, 14), sex="M", expiry=date(2027, 1, 1),
                          optional="UT4821907")
    p = mrz.parse_mrz(visa, TODAY)
    assert p["format"] == "MRVA" and p["ok"] and p["optional"] == "UT4821907"


def test_repair_ocr_confusions():
    # O read for 0 in the date, S read for 5 in the number check area
    noisy = L2[:13] + "7408I2" + L2[19:]  # I instead of 1
    fixed, parsed, notes = mrz.repair([L1, noisy], TODAY)
    assert parsed["ok"], (fixed, notes)
    assert fixed[1] == L2


def test_repair_guided_by_check_digit():
    noisy = L2.replace("L898902C3", "L898902G3")  # C -> G confusion
    fixed, parsed, notes = mrz.repair([L1, noisy], TODAY)
    assert parsed["ok"], (fixed, notes)


def test_pick_lines_from_noisy_ocr():
    raw = ["PASSPORT", "some text", L1[:40] + " <<<<", L2, "footer"]
    lines = mrz.pick_mrz_lines(raw)
    assert lines and len(lines[0]) == 44 and len(lines[1]) == 44


def test_name_normalisation():
    assert mrz.normalise_name("O'Brien-Smith") == "OBRIEN SMITH"
    assert mrz.normalise_name("José") == "JOSE"
