"""Note retrieval + eligibility gating.

Retrieval is a deterministic structured lookup (route scope and date window) over the 10 notes.
A note can justify a cost rise only if ALL gates pass:
  1. scope  : applies_to == this route, or 'All Routes'
  2. window : the note's date window overlaps this Monday-Sunday week
  3. effect : the note describes a cost RISE (notes saying 'not affected' / 'stable' / 'normal' never qualify)
  4. size   : if the note states a maximum % effect, the observed rise must not exceed it by more than the tolerance
No LLM is involved, so the verdict cannot be hallucinated or vary between runs.
"""
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .config import MAGNITUDE_TOLERANCE_PP

META_PATH = Path(__file__).with_name("notes_meta.json")


@dataclass(frozen=True)
class Verdict:
    note_id: str       # "" if unexplained
    reason: str        # "" if unexplained
    audit: str         # human-readable trail of every note considered


def load_notes(notes_csv: str) -> dict:
    """Merge the raw notes CSV with the curated metadata and check they agree."""
    raw = pd.read_csv(notes_csv)
    meta = json.loads(META_PATH.read_text())
    meta.pop("_comment", None)
    assert set(raw["note_id"]) == set(meta), "notes_meta.json and context_notes.csv have different note ids"
    notes = {}
    for r in raw.itertuples():
        m = meta[r.note_id]
        assert m["scope"] == r.applies_to, f"{r.note_id}: scope mismatch with CSV"
        assert pd.Timestamp(m["window_start"]) <= pd.Timestamp(r.date) <= pd.Timestamp(m["window_end"]), \
            f"{r.note_id}: note date not inside its own window"
        notes[r.note_id] = {**m, "date": r.date, "text": r.note,
                            "start": pd.Timestamp(m["window_start"]), "end": pd.Timestamp(m["window_end"])}
    return notes


def check_note(note: dict, route: str, week_of: pd.Timestamp, vs_own_pct: float):
    """Returns (passes: bool, why: str) for one note against one flagged route-week."""
    if note["scope"] not in (route, "All Routes"):
        return False, "wrong route"
    week_end = week_of + pd.Timedelta(days=6)
    if not (note["start"] <= week_end and note["end"] >= week_of):
        return False, "outside date window"
    if note["effect"] != "raises_cost":
        return False, f"note does not describe a cost rise ({note['effect']})"
    cap = note.get("magnitude_max_pct")
    if cap is not None and vs_own_pct > cap + MAGNITUDE_TOLERANCE_PP:
        return False, f"observed rise {vs_own_pct:+.1f}% exceeds note's stated ~{cap:.0f}% by more than {MAGNITUDE_TOLERANCE_PP:.0f}pp"
    return True, "all gates passed"


def explain(notes: dict, route: str, week_of: pd.Timestamp, vs_own_pct: float, unexplained_reason: str) -> Verdict:
    trail, hits = [], []
    for nid in sorted(notes):
        ok, why = check_note(notes[nid], route, week_of, vs_own_pct)
        trail.append(f"{nid}:{'ACCEPT' if ok else 'reject'}({why})")
        if ok:
            hits.append(nid)
    if not hits:
        return Verdict("", unexplained_reason, " | ".join(trail))
    # Deterministic tie-break: route-specific note beats an 'All Routes' note; then lowest id
    hits.sort(key=lambda n: (notes[n]["scope"] == "All Routes", n))
    n = notes[hits[0]]
    tail = ("The cost rise has a clear explanation." if "magnitude_max_pct" not in n
            else f"This week's rise ({vs_own_pct:+.1f}% vs own history) is close to that range.")
    reason = f"Matches note {hits[0]} dated {n['date']}: {n['reason']}. {tail}"
    return Verdict(hits[0], reason, " | ".join(trail))
