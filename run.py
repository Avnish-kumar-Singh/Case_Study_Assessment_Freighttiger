"""Entry point:  python run.py [--shipments ...] [--notes ...] [--out ...]"""
import argparse
import json
from pathlib import Path

from freight_watch.config import FLAG_THRESHOLD_PCT, OUTPUT_COLUMNS, UNEXPLAINED_REASON
from freight_watch.cost_engine import add_baselines, load_weekly
from freight_watch.notes import explain, load_notes

DATA = Path("data")


def build_output(shipments_csv, notes_csv):
    weekly = add_baselines(load_weekly(shipments_csv))
    notes = load_notes(notes_csv)

    # Flag rule: higher than BOTH baselines by >= threshold. First week of a route has no history -> NaN -> never flagged.
    cand = weekly[(weekly["vs_own_pct"] >= FLAG_THRESHOLD_PCT) & (weekly["vs_peer_pct"] >= FLAG_THRESHOLD_PCT)]
    cand = cand.sort_values(["week_of", "route"])

    rows, audit = [], []
    for r in cand.itertuples():
        v = explain(notes, r.route, r.week_of, r.vs_own_pct, UNEXPLAINED_REASON)
        week = r.week_of.strftime("%Y-%m-%d")
        rows.append({
            "route": r.route, "week_of": week,
            "cost_per_tonne_km": f"{r.cost_per_tonne_km:.2f}",
            "vs_own_history": f"{r.vs_own_pct:+.1f}% vs this route's past average",
            "vs_similar_routes": f"{r.vs_peer_pct:+.1f}% vs similar-length routes this week",
            "flagged": "No (justified)" if v.note_id else "Yes",
            "matched_note_id": v.note_id, "reason": v.reason})
        audit.append({"route": r.route, "week_of": week, "prior_weeks_used": r.prior_weeks_used,
                      "own_baseline": round(r.own_baseline, 4), "peer_baseline": round(r.peer_baseline, 4),
                      "notes_considered": v.audit})
    return rows, audit, weekly


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shipments", default=str(DATA / "shipment_records.csv"))
    ap.add_argument("--notes", default=str(DATA / "context_notes.csv"))
    ap.add_argument("--out", default="output/flagged_routes.csv")
    ap.add_argument("--audit", default="output/audit_trail.csv")
    a = ap.parse_args()

    import pandas as pd
    rows, audit, weekly = build_output(a.shipments, a.notes)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    # lineterminator fixed so bytes are identical across machines
    pd.DataFrame(rows, columns=OUTPUT_COLUMNS).to_csv(a.out, index=False, lineterminator="\n")
    pd.DataFrame(audit).to_csv(a.audit, index=False, lineterminator="\n")

    # Token / cost log: this pipeline makes no LLM calls.
    log = {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "model": None, "estimated_cost_usd": 0.0,
           "weekly_route_rows": len(weekly), "flagged_rows": len(rows),
           "yes": sum(r["flagged"] == "Yes" for r in rows),
           "justified": sum(r["flagged"] != "Yes" for r in rows)}
    Path(a.out).with_name("run_log.json").write_text(json.dumps(log, indent=2) + "\n")
    print(json.dumps(log))


if __name__ == "__main__":
    main()
