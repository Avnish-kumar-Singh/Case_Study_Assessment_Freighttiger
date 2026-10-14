# FreightTiger Shipping-Cost Watchdog

Flags routes whose weekly cost per tonne-km is rising abnormally, and only calls a rise "justified" when a context note
genuinely explains it for that route and that week. **The pipeline is fully deterministic and makes 0 LLM calls.**

## Run it
```
pip install pandas
python run.py                 # -> output/flagged_routes.csv, output/audit_trail.csv, output/run_log.json
python eval_harness.py        # 27 checks, exit code 1 on any failure
./repro_check.sh              # 3 fresh runs, byte-for-byte diff (Linux/Mac/Git Bash)
```
Put `shipment_records.csv`, `context_notes.csv` and `sample_output_format_v2.csv` in a `data/` folder next to `run.py`.
Input paths default to `./data/`; override with `--shipments` / `--notes`.

On Windows PowerShell (where `repro_check.sh` won't run), the reproducibility check is:
```
python run.py --out r1.csv --audit a1.csv
python run.py --out r2.csv --audit a2.csv
python run.py --out r3.csv --audit a3.csv
Get-FileHash r1.csv, r2.csv, r3.csv
```

## How it works
1. **Group & weekly cost** (`cost_engine.py`) - route = `origin-destination`; `route_type` is taken as given (asserted to be 1:1 with route).
   `shipment_date` -> Monday of its week (`week_of`). `cost_per_tonne_km = sum(freight_cost_inr) / sum(quantity_tonnes x distance_km)`
   per route-week (ratio of sums, not mean of ratios; this reproduces the sample file's 4.17 / 3.29 / 3.98 exactly).
2. **Baselines**, exactly as defined in the brief:
   - *vs own history*: mean of the route's previous <= 8 weekly values, strictly before the current week (no look-ahead).
     **Fewer than 8 prior weeks:** the first 8 weeks of every route use whatever prior weeks exist (1-7); week 1 has no history, so it can't be compared or flagged. No padding or extrapolation. The series has no calendar gaps (asserted), so "previous 8 rows" = "previous 8 weeks".
   - *vs similar routes*: mean cost/tonne-km that same week of the *other* routes with the same `route_type` (the route itself excluded).
     Note: Short and Long have only 2 routes each, so their peer baseline is a single other route and is noisier.
3. **Flag rule** ("meaningful margin" is not defined in the brief, so this is my choice): flag when the week is **>= +7% above BOTH baselines**.
   Week-to-week noise vs own history is sigma ~3-6% per route, so +7% is about 2 sigma for a typical route, and it keeps the sample's Mumbai-Pune row (+9.2%).
   Sensitivity: 5% -> 33 flags, **7% -> 15**, 10% -> 7. It's a single constant in `config.py`.
4. **Note matching** (`notes.py`) - a flagged week is "justified" only if a note passes **all four gates**:
   scope (same route or "All Routes") -> date window overlaps the Mon-Sun week -> note describes a cost *rise* -> if the note gives a size (N003: 5-7%), the observed rise must be within 3pp of it.
   Any failure -> `Yes`, blank `matched_note_id`, and the exact "No matching note found..." reason. `audit_trail.csv` shows, for each flagged row, why every one of the 10 notes was accepted or rejected.
   The justified `reason` is built from a fixed template of the note's own content + its id and date, so nothing can be invented.

### Why no LLM / vector DB (an honest trade-off)
There are only 10 notes, and each has an explicit route and date, so exact structured matching is higher-precision than embeddings and cannot
return a plausible-but-wrong note. An LLM would add cost and non-determinism to a task where the brief says a confident wrong reason scores worse than no reason.
I did read each note once and recorded its facts (window, whether it describes a cost rise) in `freight_watch/notes_meta.json`; that file is the only "judgement" input, is
short enough to review by eye, and is cross-checked against `context_notes.csv` on load. **Trade-off:** with thousands of notes, I'd put an embedding retrieval
stage (temperature-0 LLM only for extracting the metadata) in front of the same four gates. I did **not** build the stretch Q&A feature.

### Assumptions to be aware of
- **N003 (diesel, "starting this week", no end date):** applied for 8 weeks from 2025-05-05 (the same horizon after which the trailing-8-week baseline has absorbed a step change). This is my assumption, flagged in `notes_meta.json`.
- **N002 ("festival week")** = the Mon-Sun week of 2025-01-20. **N001** window is Feb 24 - Mar 8 as written, so week of 03-10 is outside it.
- Notes N004-N010 say costs were unaffected / stable / normal / not in dataset / improved roads, so they can never justify a flag (tested on every route-week).

## Results (15 flagged weeks)
11 `Yes` (unexplained), 4 `No (justified)`: Ahmedabad-Mumbai 01-20 (N002), Chennai-Bangalore 02-24 and 03-03 (N001), Mumbai-Delhi 06-02 (N003).
Worth a human look: **Mumbai-Pune Sep-Dec 2025** (6 unexplained weeks; a sustained creep) and **Chennai-Bangalore 03-10 / 03-17**,
where costs stayed ~14-22% above history *after* the flood window and repair note, so the flood doesn't explain them.
Mumbai-Delhi 05-12 (+11.6%) was *not* attributed to the diesel note because the note says 5-7%.

## How I convinced myself it's right (`eval_harness.py`, report in `output/eval_report.txt`)
- **A. Golden:** the 3 rows of `sample_output_format_v2.csv` are reproduced exactly (cost, both %s, verdict, note id).
- **B. Independent re-implementation:** a separate plain-Python (no pandas) calc agrees with the engine on all 728 route-weeks (max error 1.8e-15), and finds the identical flag set.
- **C. Labeled guardrail cases (18) + mutation test:** wrong route, week just outside window, "no effect" notes, near-miss N006 for Mumbai-Pune, oversized rise vs N003, plus "no-effect notes never pass the gate" on every one of the 5,096 combinations (728 route-weeks x 7 notes).
- **D. Invariants on the real output:** every justified row re-verified for route/window/effect and that its reason cites the note id and date; column names/order equal the sample.
Limitation: labels in C are hand-written by me from the same notes, so they check consistency with my reading, not an external ground truth.

## Reproducibility (`output/repro_check.txt`)
3 fresh processes with different `PYTHONHASHSEED`: SHA-256 of the CSV is identical for all 3 (`4a9ebcf0...2841d`), and equal to the submitted file; the audit trail is identical too.
Nothing is random; no LLM, so temperature is not applicable. CSV line endings are pinned (`\n`).

Also verified on Windows (PowerShell, Python 3.11, pandas 2.3.1): all 3 runs produced the same SHA-256, identical to the Linux hash above.

## Token / cost log (one full run over all 2,940 shipments, `output/run_log.json`)
| LLM calls | Input tokens | Output tokens | Model | Cost |
|---|---|---|---|---|
| 0 | 0 | 0 | none | Rs 0 / $0.00 |

Runtime: ~1 s on a laptop CPU.