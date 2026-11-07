#!/bin/sh
# Final fix round, final check:   sh tools/final_check.sh
# Meant to be run with Wi-Fi OFF; the first step records whether the network was really off.
#
# 1. network check (github.com must NOT answer);
# 2. a fresh `git clone` of THIS repository (local, no network) into a NEW folder next to it;
# 3. in the clone: the README build command, check_imports, the full unittest suite;
# 4. in the clone, seeds never used before: torture 500; torture 200 (timed); every random
#    mode at 100 (the judge's count for large-values, compaction, recovery-crash); targeted.
# Everything goes to evidence/final/final-check-<date-time>.txt in THIS repository.
# Takes about 25 minutes on our laptop. Each run is sequential, so the timed run has the machine.

REPO="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$REPO/evidence/final/final-check-$STAMP.txt"
DEST="$(dirname "$REPO")/final-check-clone-$STAMP"
mkdir -p "$REPO/evidence/final"

run() {  # run <label> <command...>: prints the command, the last lines, exit code and wall time
  echo
  echo "--- \$ $*"
  START=$(python3 -c 'import time; print(time.time())')
  "$@" > "$DEST/.last-run.txt" 2>&1
  CODE=$?
  END=$(python3 -c 'import time; print(time.time())')
  grep -v '^iter ' "$DEST/.last-run.txt" | tail -14
  echo "exit=$CODE  wall=$(python3 -c "print('%.1f s' % ($END - $START))")"
}

{
  echo "# Final check (final fix round): fresh clone, build, tests, torture with new seeds"
  echo "# Command: sh tools/final_check.sh"
  echo "# Started $(date '+%Y-%m-%d %H:%M:%S %Z') | $(sw_vers -productName 2>/dev/null) $(sw_vers -productVersion 2>/dev/null) | $(uname -m) | $(python3 --version)"
  echo
  echo "--- 1. network check (must NOT reach github.com):"
  if curl -sS -m 5 -o /dev/null https://github.com 2>/dev/null; then
    echo "NETWORK IS ON: github.com answered. THIS RUN DOES NOT COUNT AS OFFLINE."
  else
    echo "github.com not reachable: the network is off."
  fi
  echo
  echo "--- 2. git clone of the local repository into $DEST"
  git clone -q "$REPO" "$DEST" || exit 1
  cd "$DEST" || exit 1
  echo "cloned commit: $(git rev-parse HEAD)"
  echo "files in the clone: $(git ls-files | wc -l | tr -d ' ')"
  run python3 -m compileall -q .
  run python3 tools/check_imports.py
  run python3 -m unittest discover -s tests
  run ./torture --iterations 500 --seed 811001
  run ./torture --iterations 200 --seed 811002
  run ./torture --iterations 100 --seed 811003 --mode large-values
  run ./torture --iterations 100 --seed 811004 --mode compaction
  run ./torture --iterations 100 --seed 811005 --mode recovery-crash
  run ./torture --iterations 100 --seed 811006 --mode large-batches
  run ./torture --iterations 100 --seed 811007 --mode disk-full
  run ./torture --iterations 100 --seed 811008 --mode overlap
  run ./torture --iterations 3 --seed 811009 --mode targeted
  echo
  echo "# Finished $(date '+%Y-%m-%d %H:%M:%S %Z')"
} > "$OUT" 2>&1

echo "Done. Result: $OUT"
grep -E '^--- \$|^exit=|RESULT|INCONCLUSIVE|FAIL|TARGETED|NETWORK|network is off|Ran |^OK' "$OUT"
