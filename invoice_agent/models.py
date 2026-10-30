"""Data shapes shared across the pipeline.

The LLM fills in an ExtractedInvoice. Everything after that (math checks,
matching, risk flags, the final decision) is plain Python.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    description: str
    quantity: float
    unit_price: float
    amount: float


class ExtractedInvoice(BaseModel):
    vendor_name: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    po_number: str | None = None
    currency: str | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: float | None = None
    tax: float | None = None
    total: float | None = None
    bank_account: str | None = None


Severity = Literal["info", "warning", "critical"]


class Flag(BaseModel):
    code: str
    severity: Severity
    message: str


Decision = Literal["auto_approve", "needs_review"]


class ExtractionMeta(BaseModel):
    """What the extraction call cost, so evals can report cost and speed."""

    model: str = "unknown"
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0


class ProcessResult(BaseModel):
    invoice_id: int | None = None
    invoice: ExtractedInvoice
    vendor_id: int | None = None
    vendor_match_score: float = 0.0
    flags: list[Flag]
    decision: Decision
    meta: ExtractionMeta = Field(default_factory=ExtractionMeta)
