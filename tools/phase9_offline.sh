#!/bin/sh
# Phase 9, step 1 — run this with Wi-Fi OFF:   sh tools/phase9_offline.sh
#
# 1. checks that the network really is off (github.com must NOT be reachable);
# 2. clones THIS repository (a local clone: no network needed) into a new folder;
# 3. times the README build command and the import check in the clone;
# 4. runs the unit tests and the judge's 200-iteration torture run in the clone.
# Everything is written to evidence/final/offline-clean-clone.txt. Takes about 4 minutes.

REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$REPO/evidence/final/offline-clean-clone.txt"
DEST="$(dirname "$REPO")/offline-clone-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$REPO/evidence/final"

{
  echo "# Phase 9 step 1: clean clone, build and checks with the network OFF"
  echo "# Command: sh tools/phase9_offline.sh"
  echo "# Started $(date '+%Y-%m-%d %H:%M:%S %Z') on $(sw_vers -productVersion 2>/dev/null || uname -sr)"
  echo
  echo "--- 1. network check (must NOT reach github.com):"
  if curl -sS -m 5 -o /dev/null https://github.com 2>/dev/null; then
    echo "NETWORK IS ON: github.com answered. THIS RUN DOES NOT COUNT AS OFFLINE."
  else
    echo "github.com not reachable: the network is off."
  fi
  echo
  echo "--- 2. git clone of the local repository into $DEST"
  /usr/bin/time -p git clone -q "$REPO" "$DEST"
  cd "$DEST" || exit 1
  echo "cloned commit: $(git rev-parse --short HEAD)"
  echo
  echo "--- 3. \$ python3 -m compileall -q .     (the README build command)"
  /usr/bin/time -p python3 -m compileall -q .
  echo "exit=$?"
  echo "--- \$ python3 tools/check_imports.py"
  /usr/bin/time -p python3 tools/check_imports.py
  echo "exit=$?"
  echo
  echo "--- 4. \$ python3 -m unittest discover -s tests"
  python3 -m unittest discover -s tests 2>&1 | tail -3
  echo "--- \$ ./torture --iterations 200 --seed 990011"
  /usr/bin/time -p ./torture --iterations 200 --seed 990011 2>&1 | tail -6
  echo
  echo "# Finished $(date '+%Y-%m-%d %H:%M:%S %Z')"
} > "$OUT" 2>&1

echo "Done. Result: $OUT"
tail -25 "$OUT"
