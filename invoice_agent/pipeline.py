"""The full flow for one invoice: extract -> check -> match -> flag -> decide -> save."""
from __future__ import annotations

import sqlite3
from datetime import date

from . import db
from .checks import validate_invoice
from .extract import Document, Extractor
from .matching import (check_amount, check_bank_details, check_duplicate, find_vendor,
                       match_purchase_order)
from .models import Decision, Flag, ProcessResult
from .risk import detect_injection


def decide(flags: list[Flag]) -> Decision:
    """Only clean invoices skip the human. Any warning or critical flag goes to review.

    The system never rejects or pays on its own; a person makes every final call on flagged invoices.
    """
    if any(f.severity in ("warning", "critical") for f in flags):
        return "needs_review"
    return "auto_approve"


def process_document(
    conn: sqlite3.Connection,
    doc: Document,
    extractor: Extractor,
    today: date | None = None,
    save: bool = True,
) -> ProcessResult:
    today = today or date.today()
    invoice, meta = extractor.extract(doc)

    flags: list[Flag] = []
    flags += detect_injection(doc.text)
    flags += validate_invoice(invoice, today=today)

    vendor, score = find_vendor(conn, invoice.vendor_name)
    vendor_id = vendor["id"] if vendor else None
    if vendor is None:
        if invoice.vendor_name:
            flags.append(Flag(code="unknown_vendor", severity="critical",
                              message=f'"{invoice.vendor_name}" isn\'t an approved vendor.'))
    else:
        if score < 1.0:
            flags.append(Flag(code="vendor_name_variant", severity="info",
                              message=f'Matched "{invoice.vendor_name}" to vendor "{vendor["name"]}" '
                                      f"({score:.0%} similar)."))
        flags += check_bank_details(vendor, invoice)
        flags += check_amount(vendor, invoice)
        flags += check_duplicate(conn, vendor_id, invoice)
        flags += match_purchase_order(conn, vendor_id, invoice)

    decision = decide(flags)
    invoice_id = None
    if save:
        invoice_id = db.save_invoice(
            conn, vendor_id=vendor_id, invoice=invoice, flags=flags, decision=decision,
            status="approved" if decision == "auto_approve" else "pending_review",
            meta=meta, source_filename=doc.filename,
        )
    return ProcessResult(invoice_id=invoice_id, invoice=invoice, vendor_id=vendor_id,
                         vendor_match_score=round(score, 3), flags=flags, decision=decision, meta=meta)
