"""All tunable parameters in one place (nothing is hidden inside the logic)."""

HISTORY_WEEKS = 8            # trailing window for the "own history" baseline (fixed by the brief)
FLAG_THRESHOLD_PCT = 7.0     # flag only if the week is >= +7% above BOTH baselines
MAGNITUDE_TOLERANCE_PP = 3.0 # if a note states a max % effect, observed rise may exceed it by at most this
UNEXPLAINED_REASON = ("No matching note found for this route or date range. "
                      "Cost rise looks unexplained and worth a human review.")
OUTPUT_COLUMNS = ["route", "week_of", "cost_per_tonne_km", "vs_own_history",
                  "vs_similar_routes", "flagged", "matched_note_id", "reason"]
