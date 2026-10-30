"""SQLite storage. The schema is plain SQL so it ports to Postgres with minor changes."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    bank_account TEXT,               -- account on file; a change is a classic fraud signal
    typical_max_total REAL           -- invoices above this get a second look
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY,
    po_number TEXT NOT NULL UNIQUE,
    vendor_id INTEGER NOT NULL REFERENCES vendors(id),
    total REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY,
    vendor_id INTEGER REFERENCES vendors(id),
    vendor_name TEXT,
    invoice_number TEXT,
    invoice_date TEXT,
    po_number TEXT,
    total REAL,
    status TEXT NOT NULL,            -- pending_review | approved | rejected
    decision TEXT NOT NULL,          -- what the system recommended
    flags_json TEXT NOT NULL,
    data_json TEXT NOT NULL,         -- full extracted invoice
    source_filename TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    latency_ms REAL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_invoices_vendor_number ON invoices(vendor_id, invoice_number);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id),
    action TEXT NOT NULL,            -- approve | reject | edit
    reason TEXT,
    edited_json TEXT,                -- corrected data; corrections become new eval cases
    created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def add_vendor(conn, name: str, bank_account: str | None, typical_max_total: float | None) -> int:
    cur = conn.execute(
        "INSERT INTO vendors (name, bank_account, typical_max_total) VALUES (?, ?, ?)",
        (name, bank_account, typical_max_total),
    )
    conn.commit()
    return cur.lastrowid


def add_purchase_order(conn, po_number: str, vendor_id: int, total: float, status: str = "open") -> int:
    cur = conn.execute(
        "INSERT INTO purchase_orders (po_number, vendor_id, total, status) VALUES (?, ?, ?, ?)",
        (po_number, vendor_id, total, status),
    )
    conn.commit()
    return cur.lastrowid


def save_invoice(conn, *, vendor_id, invoice, flags, decision, status, meta, source_filename=None) -> int:
    cur = conn.execute(
        """INSERT INTO invoices (vendor_id, vendor_name, invoice_number, invoice_date, po_number, total,
               status, decision, flags_json, data_json, source_filename,
               input_tokens, output_tokens, latency_ms, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            vendor_id,
            invoice.vendor_name,
            invoice.invoice_number,
            invoice.invoice_date.isoformat() if invoice.invoice_date else None,
            invoice.po_number,
            invoice.total,
            status,
            decision,
            json.dumps([f.model_dump() for f in flags]),
            invoice.model_dump_json(),
            source_filename,
            meta.input_tokens,
            meta.output_tokens,
            meta.latency_ms,
            now_iso(),
        ),
    )
    conn.commit()
    return cur.lastrowid


def invoice_row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["flags"] = json.loads(d.pop("flags_json"))
    d["data"] = json.loads(d.pop("data_json"))
    return d
