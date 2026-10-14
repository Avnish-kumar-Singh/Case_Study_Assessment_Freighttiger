"""Eval harness. Run:  python eval_harness.py   (exit code 1 on any failure)

A. Golden rows      - the 3 rows in sample_output_format_v2.csv must be reproduced exactly (numbers, verdicts, note ids)
B. Independent calc - weekly cost/tonne-km and both baselines recomputed with plain Python (no pandas) must equal the engine
C. Labeled cases    - hand-written guardrail cases for note matching (wrong route, wrong window, 'no effect' notes, ...)
D. Output invariants- properties every row of the real output must satisfy
"""
import csv
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from freight_watch.config import FLAG_THRESHOLD_PCT, OUTPUT_COLUMNS, HISTORY_WEEKS, UNEXPLAINED_REASON
from freight_watch.notes import check_note, explain, load_notes
from run import DATA, build_output

SHIP, NOTES, SAMPLE = DATA / "shipment_records.csv", DATA / "context_notes.csv", DATA / "sample_output_format_v2.csv"
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail}" if detail and not ok else ""))


rows, audit, weekly = build_output(str(SHIP), str(NOTES))
out = {(r["route"], r["week_of"]): r for r in rows}
notes = load_notes(str(NOTES))

# ---------- A. golden rows ----------
for s in csv.DictReader(open(SAMPLE, newline="")):
    got = out.get((s["route"], s["week_of"]))
    same = got is not None and all(got[c] == s[c] for c in
                                   ["cost_per_tonne_km", "vs_own_history", "vs_similar_routes", "flagged", "matched_note_id"])
    check(f"A golden {s['route']} {s['week_of']}", same, f"expected {s} got {got}")

# ---------- B. independent recomputation ----------
tk, cost, rtype = defaultdict(float), defaultdict(float), {}
for r in csv.DictReader(open(SHIP, newline="")):
    d = date.fromisoformat(r["shipment_date"])
    wk = d - timedelta(days=d.weekday())
    k = (r["origin"] + "-" + r["destination"], wk)
    tk[k] += float(r["quantity_tonnes"]) * float(r["distance_km"])
    cost[k] += float(r["freight_cost_inr"])
    rtype[k[0]] = r["route_type"]
val = {k: cost[k] / tk[k] for k in tk}
routes = sorted({k[0] for k in val})
weeks_by_route = {r: sorted(w for (rr, w) in val if rr == r) for r in routes}
max_err, n_checked = 0.0, 0
for r in routes:
    ws = weeks_by_route[r]
    for i, w in enumerate(ws):
        row = weekly[(weekly.route == r) & (weekly.week_of == pd.Timestamp(w))].iloc[0]
        max_err = max(max_err, abs(row.cost_per_tonne_km - val[(r, w)]))
        peers = [val[(p, w)] for p in routes if p != r and rtype[p] == rtype[r]]
        max_err = max(max_err, abs(row.peer_baseline - sum(peers) / len(peers)))
        if i > 0:
            prior = [val[(r, x)] for x in ws[max(0, i - HISTORY_WEEKS):i]]
            max_err = max(max_err, abs(row.own_baseline - sum(prior) / len(prior)))
            assert row.prior_weeks_used == len(prior)
        n_checked += 1
check(f"B independent recompute matches engine on {n_checked} route-weeks (max abs err {max_err:.2e})", max_err < 1e-9)

indep_flags = set()
for r in routes:
    ws = weeks_by_route[r]
    for i, w in enumerate(ws[1:], 1):
        prior = [val[(r, x)] for x in ws[max(0, i - HISTORY_WEEKS):i]]
        peers = [val[(p, w)] for p in routes if p != r and rtype[p] == rtype[r]]
        own = (val[(r, w)] / (sum(prior) / len(prior)) - 1) * 100
        sim = (val[(r, w)] / (sum(peers) / len(peers)) - 1) * 100
        if own >= FLAG_THRESHOLD_PCT and sim >= FLAG_THRESHOLD_PCT:
            indep_flags.add((r, w.isoformat()))
check("B independent flag set == pipeline flag set", indep_flags == set(out), f"{indep_flags ^ set(out)}")

# ---------- C. labeled guardrail cases: (route, week_of, own_rise_pct, expected note id or None, why) ----------
cases = [
    ("Chennai-Bangalore", "2025-02-24", 31.5, "N001", "flood window start"),
    ("Chennai-Bangalore", "2025-03-03", 22.8, "N001", "flood window, week ends Mar 9"),
    ("Chennai-Bangalore", "2025-03-10", 22.1, None, "window ended Mar 8; N009 says normal -> must NOT match"),
    ("Chennai-Bangalore", "2025-03-17", 13.7, None, "N009 'returned to normal' is not a cost-rise note"),
    ("Chennai-Bangalore", "2025-02-17", 10.0, None, "week before flood window"),
    ("Ahmedabad-Mumbai", "2025-01-20", 29.5, "N002", "festival week"),
    ("Ahmedabad-Mumbai", "2025-01-27", 29.5, None, "week after festival week"),
    ("Delhi-Jaipur", "2025-01-20", 29.5, None, "N002 is for another route"),
    ("Mumbai-Delhi", "2024-07-29", 15.0, None, "N005 says costs not significantly affected"),
    ("Delhi-Jaipur", "2024-05-20", 15.0, None, "N007 improved roads, not a rise"),
    ("Kolkata-Bhubaneswar", "2025-06-09", 15.0, None, "N008 no disruptions"),
    ("Mumbai-Pune", "2025-09-15", 9.2, None, "N006 stable demand (+ N006 dated a week later)"),
    ("Mumbai-Pune", "2025-10-27", 15.0, None, "N010 no rate change"),
    ("Delhi-Jaipur", "2024-03-11", 15.0, None, "N004 routes not in dataset"),
    ("Mumbai-Delhi", "2025-06-02", 7.7, "N003", "diesel, inside 8-wk window, size ok"),
    ("Mumbai-Delhi", "2025-05-12", 11.6, None, "diesel note says 5-7%; +11.6% is too large to attribute"),
    ("Mumbai-Pune", "2025-04-28", 6.0, None, "week before diesel note starts"),
    ("Mumbai-Pune", "2025-07-07", 6.0, None, "after diesel window"),
]
for route, wk, own, exp, why in cases:
    v = explain(notes, route, pd.Timestamp(wk), own, UNEXPLAINED_REASON)
    check(f"C {route} {wk} -> {exp or 'unexplained'} ({why})", (v.note_id or None) == exp, f"got {v.note_id!r}")

# negative-polarity notes can never justify anything, on ANY route/week
bad = [(n, r, w) for n in notes if notes[n]["effect"] != "raises_cost"
       for r in routes for w in pd.date_range("2024-01-01", "2025-12-22", freq="W-MON")
       if check_note(notes[n], r, w, 5.0)[0]]
check("C 'no effect' notes never pass the gate for any route-week (7 routes x 104 weeks x 7 notes)", not bad, str(bad[:3]))

# mutation test: the same note moved to a different route must stop matching
mutated = {**notes, "N002": {**notes["N002"], "scope": "Delhi-Jaipur"}}
v = explain(mutated, "Ahmedabad-Mumbai", pd.Timestamp("2025-01-20"), 29.5, UNEXPLAINED_REASON)
check("C mutation: N002 re-scoped to Delhi-Jaipur no longer justifies Ahmedabad-Mumbai", v.note_id == "")

# ---------- D. invariants over the real output ----------
check("D columns/order match sample_output_format_v2.csv",
      OUTPUT_COLUMNS == list(csv.DictReader(open(SAMPLE, newline="")).fieldnames))
inv = []
for r in rows:
    wk = pd.Timestamp(r["week_of"])
    if wk.weekday() != 0: inv.append(("not Monday", r["route"], r["week_of"]))
    if r["flagged"] == "Yes":
        if r["matched_note_id"] or r["reason"] != UNEXPLAINED_REASON: inv.append(("Yes row has note/odd reason", r["route"], r["week_of"]))
    else:
        n = notes[r["matched_note_id"]]
        if r["flagged"] != "No (justified)" or n["effect"] != "raises_cost" or n["scope"] not in (r["route"], "All Routes") \
           or not (n["start"] <= wk + pd.Timedelta(days=6) and n["end"] >= wk) or r["matched_note_id"] not in r["reason"] \
           or n["date"] not in r["reason"]:
            inv.append(("justified row fails re-check", r["route"], r["week_of"]))
check(f"D all {len(rows)} output rows satisfy invariants (Monday, route/window/effect re-checked, id+date cited in reason)", not inv, str(inv))

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
