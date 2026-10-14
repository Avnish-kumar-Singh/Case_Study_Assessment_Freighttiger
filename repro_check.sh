#!/usr/bin/env bash
# Three independent, untouched runs (fresh process, different PYTHONHASHSEED) -> byte-for-byte comparison.
set -e
cd "$(dirname "$0")"
rm -rf /tmp/fc_repro && mkdir -p /tmp/fc_repro
for i in 1 2 3; do
  PYTHONHASHSEED=$i python3 run.py --out /tmp/fc_repro/run$i.csv --audit /tmp/fc_repro/audit$i.csv > /dev/null
done
sha256sum /tmp/fc_repro/run?.csv /tmp/fc_repro/audit?.csv
diff /tmp/fc_repro/run1.csv /tmp/fc_repro/run2.csv && diff /tmp/fc_repro/run2.csv /tmp/fc_repro/run3.csv \
 && diff /tmp/fc_repro/audit1.csv /tmp/fc_repro/audit3.csv \
 && diff /tmp/fc_repro/run1.csv output/flagged_routes.csv \
 && echo "REPRODUCIBLE: 3 runs byte-identical (flags, numbers, matched_note_id, reason text) and identical to the submitted CSV"
