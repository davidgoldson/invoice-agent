"""Fictional vendors and purchase orders for the demo and the eval set. All names are made up."""
from __future__ import annotations

from . import db

VENDORS = [
    {"name": "Maple Leaf Packaging Inc.", "bank_account": "001-48213-7", "typical_max_total": 5000.0,
     "products": [("Corrugated shipping box 12x9x6", 1.85), ("Kraft gift bag, medium", 0.92), ("Tissue paper ream", 14.50)]},
    {"name": "Great Lakes Sugar Co.", "bank_account": "002-77104-3", "typical_max_total": 8000.0,
     "products": [("Granulated sugar 20 kg", 38.40), ("Brown sugar 10 kg", 27.15), ("Corn syrup 5 L", 19.80)]},
    {"name": "Northside Office Supply Ltd.", "bank_account": "003-55890-1", "typical_max_total": 1500.0,
     "products": [("Printer paper, case", 54.99), ("Toner cartridge", 129.00), ("Label roll 4x6", 22.75)]},
    {"name": "Brightline Printing", "bank_account": "004-31377-9", "typical_max_total": 3000.0,
     "products": [("Flyers 8.5x11, 500 pk", 89.00), ("Business cards, 250", 45.00), ("Vinyl banner 3x6 ft", 112.50)]},
    {"name": "Kingsway Cleaning Services", "bank_account": "005-20461-4", "typical_max_total": 2500.0,
     "products": [("Office cleaning, weekly visit", 160.00), ("Window cleaning", 95.00), ("Carpet deep clean, per room", 70.00)]},
]

PURCHASE_ORDERS = [
    ("PO-2026-101", "Maple Leaf Packaging Inc.", 1200.00),
    ("PO-2026-102", "Great Lakes Sugar Co.", 3500.00),
    ("PO-2026-103", "Northside Office Supply Ltd.", 600.00),
    ("PO-2026-104", "Brightline Printing", 900.00),
    ("PO-2026-105", "Kingsway Cleaning Services", 1100.00),
]


def seed_vendors(conn) -> dict[str, int]:
    ids = {}
    for v in VENDORS:
        ids[v["name"]] = db.add_vendor(conn, v["name"], v["bank_account"], v["typical_max_total"])
    return ids


def seed_demo(conn) -> None:
    ids = seed_vendors(conn)
    for po_number, vendor_name, total in PURCHASE_ORDERS:
        db.add_purchase_order(conn, po_number, ids[vendor_name], total)
