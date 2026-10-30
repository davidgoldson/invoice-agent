"""Run the pipeline over the labeled cases and score it.

    python evals/run_evals.py              # real run: calls Claude for every case (needs ANTHROPIC_API_KEY)
    python evals/run_evals.py --mock       # no API calls: extractor returns the ground truth,
                                           # so this tests the checks/matching/flags only

Scores:
  - field accuracy (per field)       did the model read each field correctly?
  - catch rate (per planted issue)   did the system raise the flag a correct system must raise?
  - false alarm rate                 clean invoices that were wrongly sent to review
  - decision accuracy                auto-approve vs needs-review matches the label
  - straight-through rate            share of clean invoices handled with no human
  - tokens, latency, cost per invoice
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from invoice_agent import db  # noqa: E402
from invoice_agent.extract import Document, ExtractionError  # noqa: E402
from invoice_agent.models import ExtractedInvoice, ExtractionMeta  # noqa: E402
from invoice_agent.pipeline import process_document  # noqa: E402
from invoice_agent.sample_data import seed_vendors  # noqa: E402

HERE = Path(__file__).parent
FIELDS = ["vendor_name", "invoice_number", "invoice_date", "po_number", "subtotal", "tax", "total",
          "bank_account", "line_items"]
CLEAN_ISSUES = {"clean", "vendor_variant", "messy_layout"}


class OracleExtractor:
    """Returns the ground truth. Lets you test everything after extraction without spending API calls."""

    def __init__(self, truth: dict):
        self.truth = truth

    def extract(self, doc: Document):
        return ExtractedInvoice.model_validate(self.truth), ExtractionMeta(model="oracle")


def _norm(field: str, v):
    if v is None:
        return None
    if field in ("subtotal", "tax", "total"):
        return round(float(v), 2)
    if field == "invoice_date":
        return str(v)[:10]
    if field == "bank_account":
        return "".join(ch for ch in str(v) if ch.isdigit())
    if field == "line_items":
        return sorted((round(float(l["amount"]), 2), round(float(l["quantity"]), 3)) for l in v)
    return " ".join(str(v).lower().split())


def build_db(case: dict):
    conn = db.connect(":memory:")
    ids = seed_vendors(conn)
    for po in case["setup"]["purchase_orders"]:
        db.add_purchase_order(conn, po["po_number"], ids[po["vendor_name"]], po["total"])
    for prior in case["setup"]["prior_invoices"]:
        conn.execute(
            "INSERT INTO invoices (vendor_id, vendor_name, invoice_number, invoice_date, total, status, decision,"
            " flags_json, data_json, created_at) VALUES (?, ?, ?, ?, ?, 'approved', 'auto_approve', '[]', '{}', ?)",
            (ids[prior["vendor_name"]], prior["vendor_name"], prior["invoice_number"], prior["invoice_date"],
             prior["total"], db.now_iso()),
        )
    conn.commit()
    return conn


def run(cases: list[dict], mock: bool, model: str | None) -> dict:
    extractor = None
    if not mock:
        from invoice_agent.extract import ClaudeExtractor
        extractor = ClaudeExtractor(model=model)

    field_hits = defaultdict(int)
    field_total = defaultdict(int)
    catch = defaultdict(lambda: [0, 0])     # issue -> [caught, total]
    clean_total = clean_flagged = 0
    decisions_right = 0
    tokens_in = tokens_out = 0
    latencies = []
    errors = []
    failures = []

    for case in cases:
        conn = build_db(case)
        doc = Document(filename=f"{case['id']}.txt", media_type="text/plain",
                       data=case["text"].encode(), text=case["text"])
        ex = OracleExtractor(case["truth"]) if mock else extractor
        try:
            result = process_document(conn, doc, ex, today=date.fromisoformat(case["reference_date"]), save=False)
        except ExtractionError as e:
            errors.append({"id": case["id"], "error": str(e)})
            continue

        got = result.invoice.model_dump(mode="json")
        for f in FIELDS:
            field_total[f] += 1
            if _norm(f, got.get(f)) == _norm(f, case["truth"].get(f)):
                field_hits[f] += 1

        raised = {fl.code for fl in result.flags}
        issue = case["issue"]
        if issue in CLEAN_ISSUES:
            clean_total += 1
            if result.decision != "auto_approve":
                clean_flagged += 1
                failures.append({"id": case["id"], "issue": issue, "problem": "false alarm",
                                 "flags": sorted(raised - {"no_po_reference", "vendor_name_variant"})})
        else:
            ok = all(code in raised for code in case["expected_flags"])
            catch[issue][0] += ok
            catch[issue][1] += 1
            if not ok:
                failures.append({"id": case["id"], "issue": issue, "problem": "missed",
                                 "expected": case["expected_flags"], "flags": sorted(raised)})
        decisions_right += result.decision == case["expected_decision"]

        tokens_in += result.meta.input_tokens
        tokens_out += result.meta.output_tokens
        latencies.append(result.meta.latency_ms)

    n = len(cases) - len(errors)
    caught = sum(c for c, _ in catch.values())
    planted = sum(t for _, t in catch.values())
    in_price = os.environ.get("INPUT_COST_PER_MTOK")
    out_price = os.environ.get("OUTPUT_COST_PER_MTOK")
    cost_per_invoice = None
    if in_price and out_price and n:
        cost_per_invoice = round((tokens_in * float(in_price) + tokens_out * float(out_price)) / 1e6 / n, 5)

    return {
        "mode": "mock (oracle extractor — not a model result)" if mock else f"live ({extractor.model})",
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "cases": len(cases),
        "extraction_errors": len(errors),
        "field_accuracy": {f: round(field_hits[f] / field_total[f], 3) for f in FIELDS if field_total[f]},
        "catch_rate_overall": round(caught / planted, 3) if planted else None,
        "catch_rate_by_issue": {k: f"{c}/{t}" for k, (c, t) in sorted(catch.items())},
        "false_alarm_rate": round(clean_flagged / clean_total, 3) if clean_total else None,
        "straight_through_rate_clean": round((clean_total - clean_flagged) / clean_total, 3) if clean_total else None,
        "decision_accuracy": round(decisions_right / n, 3) if n else None,
        "avg_input_tokens": round(tokens_in / n) if n else 0,
        "avg_output_tokens": round(tokens_out / n) if n else 0,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
        "cost_per_invoice_usd": cost_per_invoice,
        "failures": failures,
        "errors": errors,
    }


def print_report(r: dict) -> None:
    print(f"\nMode: {r['mode']}   Cases: {r['cases']}   Extraction errors: {r['extraction_errors']}\n")
    print("Field accuracy")
    for f, v in r["field_accuracy"].items():
        print(f"  {f:<16} {v:.1%}")
    print(f"\nPlanted problems caught: {r['catch_rate_overall']:.1%}" if r["catch_rate_overall"] is not None else "")
    for k, v in r["catch_rate_by_issue"].items():
        print(f"  {k:<16} {v}")
    print(f"\nFalse alarm rate (clean invoices sent to review): {r['false_alarm_rate']:.1%}")
    print(f"Straight-through rate (clean invoices):           {r['straight_through_rate_clean']:.1%}")
    print(f"Decision accuracy:                                {r['decision_accuracy']:.1%}")
    print(f"Avg tokens in/out: {r['avg_input_tokens']}/{r['avg_output_tokens']}   Avg latency: {r['avg_latency_ms']} ms"
          + (f"   Cost/invoice: ${r['cost_per_invoice_usd']}" if r["cost_per_invoice_usd"] is not None else ""))
    if r["failures"]:
        print(f"\nFailures ({len(r['failures'])}), first 10:")
        for f in r["failures"][:10]:
            print(f"  {f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="skip the model; score the rules only")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--cases", type=Path, default=HERE / "cases.jsonl")
    ap.add_argument("--min-catch-rate", type=float, default=None, help="exit non-zero below this (for CI)")
    args = ap.parse_args()

    if not args.cases.exists():
        sys.exit(f"No eval cases at {args.cases}. Run: python evals/generate.py")
    cases = [json.loads(line) for line in args.cases.read_text().splitlines() if line.strip()]
    if args.limit:
        cases = cases[: args.limit]

    start = time.perf_counter()
    report = run(cases, mock=args.mock, model=args.model)
    report["wall_seconds"] = round(time.perf_counter() - start, 1)
    print_report(report)

    out = HERE / "results" / f"{datetime.now():%Y%m%d-%H%M%S}{'-mock' if args.mock else ''}.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"\nSaved {out}")

    if args.min_catch_rate is not None and (report["catch_rate_overall"] or 0) < args.min_catch_rate:
        sys.exit(f"Catch rate {report['catch_rate_overall']} is below {args.min_catch_rate}")


if __name__ == "__main__":
    main()
