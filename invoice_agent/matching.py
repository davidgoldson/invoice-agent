"""Matching the invoice against the business's own records: vendors, purchase orders, past invoices."""
from __future__ import annotations

import re
import sqlite3
from difflib import SequenceMatcher

from .models import ExtractedInvoice, Flag

FUZZY_VENDOR_THRESHOLD = 0.85
PO_OVERAGE_TOLERANCE = 0.02  # invoice may exceed the PO by up to 2%

_SUFFIXES = re.compile(r"\b(inc|incorporated|ltd|limited|llc|corp|corporation|co|company)\b")


def normalize_name(name: str) -> str:
    n = name.lower()
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    n = _SUFFIXES.sub(" ", n)
    return " ".join(n.split())


def digits_only(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def find_vendor(conn: sqlite3.Connection, name: str | None) -> tuple[sqlite3.Row | None, float]:
    """Return the best-matching vendor and a 0-1 match score."""
    if not name:
        return None, 0.0
    target = normalize_name(name)
    best, best_score = None, 0.0
    for row in conn.execute("SELECT * FROM vendors"):
        score = SequenceMatcher(None, target, normalize_name(row["name"])).ratio()
        if score > best_score:
            best, best_score = row, score
    if best_score >= FUZZY_VENDOR_THRESHOLD:
        return best, best_score
    return None, best_score


def check_bank_details(vendor: sqlite3.Row, inv: ExtractedInvoice) -> list[Flag]:
    on_file = digits_only(vendor["bank_account"])
    printed = digits_only(inv.bank_account)
    if on_file and printed and on_file != printed:
        return [Flag(code="bank_details_changed", severity="critical",
                     message=f"Payment details on the invoice ({inv.bank_account}) don't match the account on file "
                             f"for {vendor['name']}. Confirm with the vendor by phone before paying.")]
    return []


def check_amount(vendor: sqlite3.Row, inv: ExtractedInvoice) -> list[Flag]:
    limit = vendor["typical_max_total"]
    if limit and inv.total is not None and inv.total > limit:
        return [Flag(code="unusual_amount", severity="warning",
                     message=f"Total {inv.total:,.2f} is above this vendor's usual maximum of {limit:,.2f}.")]
    return []


def check_duplicate(conn: sqlite3.Connection, vendor_id: int, inv: ExtractedInvoice) -> list[Flag]:
    if inv.invoice_number:
        row = conn.execute(
            "SELECT id, status FROM invoices WHERE vendor_id = ? AND lower(invoice_number) = lower(?)",
            (vendor_id, inv.invoice_number),
        ).fetchone()
        if row:
            return [Flag(code="duplicate_invoice", severity="critical",
                         message=f"Invoice {inv.invoice_number} from this vendor was already received "
                                 f"(record #{row['id']}, {row['status']}).")]
    # Same vendor, same amount, same date but a different number is also suspicious
    if inv.total is not None and inv.invoice_date is not None:
        row = conn.execute(
            "SELECT id FROM invoices WHERE vendor_id = ? AND abs(total - ?) < 0.01 AND invoice_date = ?",
            (vendor_id, inv.total, inv.invoice_date.isoformat()),
        ).fetchone()
        if row:
            return [Flag(code="possible_duplicate", severity="warning",
                         message=f"Another invoice from this vendor has the same date and total (record #{row['id']}).")]
    return []


def match_purchase_order(conn: sqlite3.Connection, vendor_id: int, inv: ExtractedInvoice) -> list[Flag]:
    if not inv.po_number:
        return [Flag(code="no_po_reference", severity="info",
                     message="The invoice doesn't reference a purchase order.")]
    po = conn.execute("SELECT * FROM purchase_orders WHERE lower(po_number) = lower(?)",
                      (inv.po_number,)).fetchone()
    if po is None:
        return [Flag(code="po_not_found", severity="critical",
                     message=f"Purchase order {inv.po_number} isn't in the system.")]
    flags: list[Flag] = []
    if po["vendor_id"] != vendor_id:
        flags.append(Flag(code="po_vendor_mismatch", severity="critical",
                          message=f"Purchase order {inv.po_number} belongs to a different vendor."))
    if po["status"] != "open":
        flags.append(Flag(code="po_not_open", severity="warning",
                          message=f"Purchase order {inv.po_number} is {po['status']}, not open."))
    if inv.total is not None and inv.total > po["total"] * (1 + PO_OVERAGE_TOLERANCE):
        flags.append(Flag(code="po_amount_exceeded", severity="critical",
                          message=f"Invoice total {inv.total:,.2f} is more than purchase order "
                                  f"{inv.po_number} allows ({po['total']:,.2f})."))
    return flags
