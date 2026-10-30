"""Fictional seed data shared by the sample catalog and the SIMULATED watchlist.
Nothing here refers to a real person or a real document."""

WATCHLIST_PERSONS = [
    {"ref": "SIM-W-0001", "name": "VICTOR ALDRIDGE KROWE", "dob": "1980-02-03", "category": "WANTED",
     "note": "Fictional entry for demonstration"},
    {"ref": "SIM-W-0002", "name": "MARA SOLENNE VASK", "dob": "1991-11-30", "category": "ENTRY BAN",
     "note": "Fictional entry for demonstration"},
    {"ref": "SIM-W-0003", "name": "DEMIAN OSTROVE LAHR", "dob": "1976-05-19", "category": "ALERT - SECONDARY",
     "note": "Fictional entry for demonstration"},
]

# lost / stolen document registry (SIMULATED SLTD-style list)
WATCHLIST_DOCUMENTS = [
    {"ref": "SIM-D-0001", "number": "UT990314", "doc_type": "passport", "status": "STOLEN",
     "note": "Fictional entry for demonstration"},
    {"ref": "SIM-D-0002", "number": "UD8810007", "doc_type": "id_card", "status": "LOST",
     "note": "Fictional entry for demonstration"},
]
