"""Checks the Claude extractor's request and response handling without calling the API."""
from types import SimpleNamespace

import pytest

from invoice_agent.extract import ClaudeExtractor, Document, ExtractionError, load_document

GOOD_INPUT = {
    "vendor_name": "Brightline Printing", "invoice_number": "BP-1", "invoice_date": "2026-10-02",
    "po_number": None, "currency": "CAD",
    "line_items": [{"description": "Flyers", "quantity": 2, "unit_price": 89.0, "amount": 178.0}],
    "subtotal": 178.0, "tax": 23.14, "total": 201.14, "bank_account": None,
}


class FakeMessages:
    def __init__(self, content):
        self.content = content
        self.last_request = None

    def create(self, **kwargs):
        self.last_request = kwargs
        return SimpleNamespace(content=self.content, usage=SimpleNamespace(input_tokens=900, output_tokens=120))


def extractor_with(content):
    ex = ClaudeExtractor.__new__(ClaudeExtractor)
    ex.model = "test-model"
    ex.client = SimpleNamespace(messages=FakeMessages(content))
    return ex


def test_parses_tool_output_and_forces_the_tool():
    ex = extractor_with([SimpleNamespace(type="tool_use", input=GOOD_INPUT)])
    inv, meta = ex.extract(load_document("inv.txt", b"BRIGHTLINE PRINTING ..."))
    assert inv.total == 201.14 and str(inv.invoice_date) == "2026-10-02"
    assert meta.input_tokens == 900
    req = ex.client.messages.last_request
    assert req["tools"][0]["name"] == "record_invoice"


def test_images_and_pdfs_are_sent_as_files():
    ex = extractor_with([SimpleNamespace(type="tool_use", input=GOOD_INPUT)])
    ex.extract(Document("scan.png", "image/png", b"\x89PNG..."))
    assert ex.client.messages.last_request["messages"][0]["content"][0]["type"] == "image"
    ex.extract(Document("inv.pdf", "application/pdf", b"%PDF..."))
    assert ex.client.messages.last_request["messages"][0]["content"][0]["type"] == "document"


def test_missing_tool_call_raises():
    ex = extractor_with([SimpleNamespace(type="text", text="I can't read this.")])
    with pytest.raises(ExtractionError):
        ex.extract(load_document("inv.txt", b"..."))


def test_wrong_shape_raises():
    ex = extractor_with([SimpleNamespace(type="tool_use", input={**GOOD_INPUT, "total": "a lot"})])
    with pytest.raises(ExtractionError):
        ex.extract(load_document("inv.txt", b"..."))
