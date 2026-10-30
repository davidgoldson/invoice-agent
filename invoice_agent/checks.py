"""Deterministic checks. No AI here: every number the model reads gets verified by code."""
from __future__ import annotations

from datetime import date, timedelta

from .models import ExtractedInvoice, Flag

ONTARIO_HST = 0.13
MONEY_TOLERANCE = 0.02  # dollars; allows for rounding on printed invoices


def _close(a: float, b: float, tol: float = MONEY_TOLERANCE) -> bool:
    return abs(a - b) <= tol


def validate_invoice(
    inv: ExtractedInvoice,
    today: date,
    tax_rate: float = ONTARIO_HST,
) -> list[Flag]:
    flags: list[Flag] = []

    # Required fields
    for field in ("vendor_name", "invoice_number", "invoice_date", "total"):
        if getattr(inv, field) in (None, ""):
            flags.append(Flag(code="missing_field", severity="critical",
                              message=f"Could not find the {field.replace('_', ' ')} on the invoice."))

    # Each line: quantity x unit price = amount
    for i, line in enumerate(inv.line_items, start=1):
        expected = round(line.quantity * line.unit_price, 2)
        if not _close(expected, line.amount):
            flags.append(Flag(code="line_math_error", severity="warning",
                              message=f"Line {i} ({line.description}): {line.quantity} x {line.unit_price:.2f} "
                                      f"= {expected:.2f}, but the invoice says {line.amount:.2f}."))

    # Lines add up to the subtotal
    if inv.line_items and inv.subtotal is not None:
        line_sum = round(sum(l.amount for l in inv.line_items), 2)
        if not _close(line_sum, inv.subtotal):
            flags.append(Flag(code="subtotal_mismatch", severity="critical",
                              message=f"Line items add up to {line_sum:.2f}, but the subtotal says {inv.subtotal:.2f}."))

    # Tax is the expected rate
    if inv.subtotal is not None and inv.tax is not None:
        expected_tax = round(inv.subtotal * tax_rate, 2)
        if inv.tax == 0:
            flags.append(Flag(code="no_tax_charged", severity="info",
                              message="No tax was charged. Check whether this vendor is tax-exempt."))
        elif not _close(expected_tax, inv.tax, tol=0.05):
            flags.append(Flag(code="tax_rate_mismatch", severity="warning",
                              message=f"Tax is {inv.tax:.2f}; {tax_rate:.0%} of the subtotal would be {expected_tax:.2f}."))

    # Subtotal + tax = total
    if inv.subtotal is not None and inv.tax is not None and inv.total is not None:
        expected_total = round(inv.subtotal + inv.tax, 2)
        if not _close(expected_total, inv.total):
            flags.append(Flag(code="total_mismatch", severity="critical",
                              message=f"Subtotal + tax = {expected_total:.2f}, but the total says {inv.total:.2f}."))

    # Date sanity
    if inv.invoice_date is not None:
        if inv.invoice_date > today:
            flags.append(Flag(code="future_date", severity="warning",
                              message=f"Invoice is dated {inv.invoice_date}, which is in the future."))
        elif inv.invoice_date < today - timedelta(days=365):
            flags.append(Flag(code="stale_invoice", severity="warning",
                              message=f"Invoice is dated {inv.invoice_date}, more than a year ago."))

    return flags
