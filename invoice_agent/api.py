"""HTTP API and review screen.

Run:  uvicorn invoice_agent.api:app --reload
"""
from __future__ import annotations

import csv
import io
import json
import os
from collections import Counter
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from . import db
from .extract import ExtractionError, Extractor, load_document
from .models import ExtractedInvoice
from .pipeline import process_document
from .sample_data import seed_demo

STATIC = Path(__file__).parent / "static"


class ReviewRequest(BaseModel):
    action: Literal["approve", "reject", "edit"]
    reason: str | None = None
    corrected: ExtractedInvoice | None = None  # required for "edit"; the invoice is then approved


def create_app(db_path: str | None = None, extractor: Extractor | None = None, seed: bool = True) -> FastAPI:
    conn = db.connect(db_path or os.environ.get("DATABASE_PATH", "invoices.db"))
    if seed and conn.execute("SELECT count(*) FROM vendors").fetchone()[0] == 0:
        seed_demo(conn)

    app = FastAPI(title="Invoice Agent")
    state = {"extractor": extractor}

    def get_extractor() -> Extractor:
        if state["extractor"] is None:
            from .extract import ClaudeExtractor
            state["extractor"] = ClaudeExtractor()
        return state["extractor"]

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.post("/invoices")
    async def upload(file: UploadFile = File(...)):
        data = await file.read()
        try:
            doc = load_document(file.filename or "upload", data)
            result = process_document(conn, doc, get_extractor())
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except ExtractionError as e:
            raise HTTPException(status_code=502, detail=str(e))
        return result.model_dump(mode="json")

    @app.get("/invoices")
    def list_invoices(status: str | None = None):
        if status:
            rows = conn.execute("SELECT * FROM invoices WHERE status = ? ORDER BY id DESC", (status,))
        else:
            rows = conn.execute("SELECT * FROM invoices ORDER BY id DESC")
        return [db.invoice_row_to_dict(r) for r in rows]

    @app.get("/invoices/{invoice_id}")
    def get_invoice(invoice_id: int):
        row = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Invoice not found.")
        out = db.invoice_row_to_dict(row)
        out["reviews"] = [dict(r) for r in conn.execute(
            "SELECT action, reason, created_at FROM reviews WHERE invoice_id = ? ORDER BY id", (invoice_id,))]
        return out

    @app.post("/invoices/{invoice_id}/review")
    def review(invoice_id: int, req: ReviewRequest):
        row = conn.execute("SELECT status FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Invoice not found.")
        if req.action == "reject" and not (req.reason or "").strip():
            raise HTTPException(status_code=400, detail="Add a reason when rejecting, so the decision is on record.")
        if req.action == "edit" and req.corrected is None:
            raise HTTPException(status_code=400, detail="An edit needs the corrected invoice fields.")

        edited_json = req.corrected.model_dump_json() if req.corrected else None
        conn.execute(
            "INSERT INTO reviews (invoice_id, action, reason, edited_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (invoice_id, req.action, req.reason, edited_json, db.now_iso()),
        )
        new_status = "rejected" if req.action == "reject" else "approved"
        if req.corrected:
            c = req.corrected
            conn.execute(
                """UPDATE invoices SET data_json = ?, vendor_name = ?, invoice_number = ?, invoice_date = ?,
                       po_number = ?, total = ? WHERE id = ?""",
                (edited_json, c.vendor_name, c.invoice_number,
                 c.invoice_date.isoformat() if c.invoice_date else None, c.po_number, c.total, invoice_id),
            )
        conn.execute("UPDATE invoices SET status = ? WHERE id = ?", (new_status, invoice_id))
        conn.commit()
        return {"id": invoice_id, "status": new_status}

    @app.get("/export.csv", response_class=PlainTextResponse)
    def export_csv():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["invoice_id", "vendor", "invoice_number", "invoice_date", "po_number", "subtotal", "tax", "total"])
        for r in conn.execute("SELECT * FROM invoices WHERE status = 'approved' ORDER BY invoice_date"):
            d = json.loads(r["data_json"])
            w.writerow([r["id"], d.get("vendor_name"), d.get("invoice_number"), d.get("invoice_date"),
                        d.get("po_number"), d.get("subtotal"), d.get("tax"), d.get("total")])
        return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=approved_invoices.csv"})

    @app.get("/metrics")
    def metrics():
        rows = [db.invoice_row_to_dict(r) for r in conn.execute("SELECT * FROM invoices")]
        n = len(rows)
        by_status = Counter(r["status"] for r in rows)
        flag_counts = Counter(f["code"] for r in rows for f in r["flags"])
        corrected = conn.execute("SELECT count(DISTINCT invoice_id) FROM reviews WHERE action = 'edit'").fetchone()[0]
        return {
            "invoices": n,
            "by_status": dict(by_status),
            "straight_through_rate": round(sum(r["decision"] == "auto_approve" for r in rows) / n, 3) if n else None,
            "human_correction_rate": round(corrected / n, 3) if n else None,
            "avg_latency_ms": round(sum(r["latency_ms"] or 0 for r in rows) / n, 1) if n else None,
            "avg_input_tokens": round(sum(r["input_tokens"] or 0 for r in rows) / n) if n else None,
            "avg_output_tokens": round(sum(r["output_tokens"] or 0 for r in rows) / n) if n else None,
            "flag_counts": dict(flag_counts.most_common()),
        }

    return app


# Module-level app for `uvicorn invoice_agent.api:app`. Built lazily so importing this module in tests
# doesn't create invoices.db.
def __getattr__(name):
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
