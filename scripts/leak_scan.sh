#!/usr/bin/env bash
# leak_scan.sh - block private identifiers and secrets before they reach the repo.
#
# Usage: leak_scan.sh (--staged | --all) [--generic-only]
#   --staged        scan the lines added in `git diff --cached` plus the staged paths
#   --all           scan every non-binary file from `git ls-files --cached --others --exclude-standard`
#   --generic-only  use the generic patterns only, with no denylist and no warning about it
#                   (public CI has no access to the list)
#
# Exit codes: 0 clean, 1 hits found, 2 usage or configuration error (fail-closed).
#
# Two pattern families:
#   * generic  - public, defined in this file (IPs, home paths, key and token shapes)
#   * denylist - private ERE list, one pattern per line, '#' comments allowed, case-insensitive.
#                Read from $TWOTRACK_DENYLIST or ~/.config/twotrack/denylist.txt and never
#                stored in the repo.
#
# The denylist exists only on the maintainer's machine, so a contributor's scan has none:
#   * a missing or unreadable file, or one with no active pattern, makes the scan print one
#     warning to stderr and run the generic patterns only (`make leak-scan` and the pre-commit
#     hook work on a fresh clone);
#   * on a machine where `git config --bool twotrack.requireDenylist` is true the same case is
#     exit 2, so the scan cannot quietly skip the list it is meant to apply. The maintainer sets
#     it once for every clone:  git config --global twotrack.requireDenylist true
#   * a list that exists but is broken (a line that is not a valid ERE, or one that matches the
#     empty string) is exit 2 either way.
#
# Output is `path:line: [pattern-id]`. Denylist hits print the denylist line number, never the
# matched text, so CI logs and terminal scrollback cannot re-leak what was blocked.
#
# This file is skipped by the generic scan (its own pattern definitions would self-match);
# the denylist still applies to it.
#
# Portability: bash 3.2 (macOS) and Linux; no associative arrays, no mapfile.

export LC_ALL=C
set -u

SELF_PATH="scripts/leak_scan.sh"
DENYLIST_FILE="${TWOTRACK_DENYLIST:-$HOME/.config/twotrack/denylist.txt}"

MODE=""
USE_DENYLIST=1

usage() {
  echo "usage: leak_scan.sh (--staged | --all) [--generic-only]" >&2
  exit 2
}

for arg in "$@"; do
  case "$arg" in
    --staged | --all)
      [ -z "$MODE" ] || usage
      MODE=${arg#--}
      ;;
    --generic-only) USE_DENYLIST=0 ;;
    *) usage ;;
  esac
done
[ -n "$MODE" ] || usage

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "leak_scan: not inside a git repository" >&2
  exit 2
}
cd "$ROOT" || exit 2

W=$(mktemp -d "${TMPDIR:-/tmp}/leak_scan.XXXXXX") || exit 2
trap 'rm -rf "$W"' EXIT
HITS="$W/hits"
: > "$HITS"

# ---------------------------------------------------------------- generic patterns
# GEN_IDS[i] names GEN_PATS[i]; keep the two lists in step. The token shapes after private-key
# are the ones the plain sk- rule cannot see (a hyphen or underscore follows the prefix) or that
# belong to another provider. Left boundaries keep a prefix inside a longer word (task-ant-...)
# from matching.
GEN_IDS=(home-path-macos home-path-linux gmail openai-key openai-project-key
  aws-access-key aliyun-access-key private-key
  anthropic-key openai-service-key openrouter-key github-token github-fine-grained-token
  aws-temp-access-key google-api-key huggingface-token slack-token npm-token pypi-token)
GEN_PATS=(
  '/Users/[A-Za-z]'
  '/home/[a-z]+/'
  '@gmail\.com'
  '(^|[^A-Za-z0-9])sk-[A-Za-z0-9]{20,}'
  'sk-proj-[A-Za-z0-9_-]{20,}'
  'AKIA[0-9A-Z]{16}'
  'LTAI[0-9A-Za-z]{12,}'
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'
  '(^|[^A-Za-z0-9])sk-ant-[A-Za-z0-9_-]{20,}'
  '(^|[^A-Za-z0-9])sk-(svcacct|admin)-[A-Za-z0-9_-]{20,}'
  '(^|[^A-Za-z0-9])sk-or-v1-[0-9a-f]{32,}'
  '(^|[^A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{36,}'
  '(^|[^A-Za-z0-9])github_pat_[A-Za-z0-9_]{22,}'
  '(^|[^A-Za-z0-9])ASIA[0-9A-Z]{16}([^A-Za-z0-9]|$)'
  '(^|[^A-Za-z0-9_-])AIza[0-9A-Za-z_-]{35}'
  '(^|[^A-Za-z0-9_])hf_[A-Za-z0-9]{34,}'
  '(^|[^A-Za-z0-9])xox[abposr]-[0-9A-Za-z-]{10,}'
  '(^|[^A-Za-z0-9_])npm_[A-Za-z0-9]{36}'
  'pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{50,}'
)
if [ "${#GEN_IDS[@]}" -ne "${#GEN_PATS[@]}" ]; then
  echo "leak_scan: internal error: GEN_IDS and GEN_PATS differ in length" >&2
  exit 2
fi
GEN_N=${#GEN_PATS[@]}
GEN_PATFILE="$W/generic.pat"
: > "$GEN_PATFILE"
for p in "${GEN_PATS[@]}"; do printf '%s\n' "$p" >> "$GEN_PATFILE"; done

# Public IPv4: extract dotted-number runs, keep runs of exactly four octets (<=255), skip runs
# glued to a preceding word character, '.' or '-' (version strings, wheel file names), then drop
# non-public ranges. Prints the 1-based line number of each line holding a public address.
IPV4_AWK='
function nonpublic(a, b, c) {
  if (a == 0 || a == 10 || a == 127 || a >= 224) return 1
  if (a == 169 && b == 254) return 1
  if (a == 172 && b >= 16 && b <= 31) return 1
  if (a == 192 && b == 168) return 1
  if (a == 100 && b >= 64 && b <= 127) return 1
  if (a == 192 && b == 0 && c == 2) return 1
  if (a == 198 && b == 51 && c == 100) return 1
  if (a == 203 && b == 0 && c == 113) return 1
  return 0
}
{
  rest = $0
  found = 0
  while (!found && match(rest, /[0-9]+(\.[0-9]+)+/)) {
    run = substr(rest, RSTART, RLENGTH)
    prev = (RSTART > 1) ? substr(rest, RSTART - 1, 1) : ""
    rest = substr(rest, RSTART + RLENGTH)
    if (prev ~ /[A-Za-z0-9_.-]/) continue
    if (split(run, o, ".") != 4) continue
    ok = 1
    for (i = 1; i <= 4; i++) if (length(o[i]) > 3 || o[i] + 0 > 255) ok = 0
    if (ok && !nonpublic(o[1] + 0, o[2] + 0, o[3] + 0)) found = 1
  }
  if (found) print NR
}
'

# ---------------------------------------------------------------- denylist
DENY_N=0
DENY_PATFILE="$W/deny.pat"
: > "$DENY_PATFILE"
DENY_PAT=()
DENY_LN=()

# True on a machine that must not scan without the private list: the maintainer's (see the header).
denylist_required() {
  local value rc=0
  value=$(git config --bool twotrack.requireDenylist 2>/dev/null) || rc=$?
  case $rc in
    0) [ "$value" = true ] ;;
    1) return 1 ;; # not set
    *)
      echo "leak_scan: twotrack.requireDenylist must be true or false" >&2
      exit 2
      ;;
  esac
}

# denylist_unusable <why>: the private list cannot be applied. Where it is required that is exit 2;
# anywhere else the scan warns once and carries on with the generic patterns.
denylist_unusable() {
  if denylist_required; then
    echo "leak_scan: $1, and twotrack.requireDenylist is true (--generic-only skips the list)" >&2
    exit 2
  fi
  echo "leak_scan: warning: $1; scanning with the generic patterns only (--generic-only silences this)" >&2
  USE_DENYLIST=0
}

load_denylist() {
  if [ ! -r "$DENYLIST_FILE" ]; then
    denylist_unusable "no readable private denylist (\$TWOTRACK_DENYLIST or ~/.config/twotrack/denylist.txt)"
    return
  fi
  local line trimmed rc n=0
  while IFS= read -r line || [ -n "$line" ]; do
    n=$((n + 1))
    line=${line%$'\r'}
    trimmed=${line#"${line%%[![:space:]]*}"}
    case "$trimmed" in '' | '#'*) continue ;; esac
    # Validate without echoing the pattern: a bad regex or one matching the empty string
    # would silently disable or flood the scan.
    grep -E -e "$line" < /dev/null > /dev/null 2>&1
    rc=$?
    if [ "$rc" -ge 2 ]; then
      echo "leak_scan: denylist line $n is not a valid ERE" >&2
      exit 2
    fi
    if grep -E -e "$line" <<< "" > /dev/null 2>&1; then
      echo "leak_scan: denylist line $n matches the empty string" >&2
      exit 2
    fi
    DENY_PAT[$DENY_N]=$line
    DENY_LN[$DENY_N]=$n
    DENY_N=$((DENY_N + 1))
    printf '%s\n' "$line" >> "$DENY_PATFILE"
  done < "$DENYLIST_FILE"
  if [ "$DENY_N" -eq 0 ]; then
    denylist_unusable "the private denylist has no active patterns"
  fi
}

if [ "$USE_DENYLIST" -eq 1 ]; then load_denylist; fi

# ---------------------------------------------------------------- scanning
# record <name> <line-in-content> <map-file|""> <pattern-id>
record() {
  local real=$2
  if [ -n "$3" ]; then real=$(sed -n "${2}p" "$3"); fi
  printf '%s:%s: [%s]\n' "$1" "$real" "$4" >> "$HITS"
}

# scan_unit <display-name> <content-file> <map-file|""> <skip-generic 0|1>
# map-file holds the real line number for each content line (staged diffs only).
scan_unit() {
  local name=$1 content=$2 map=$3 skip_generic=$4 i k
  if [ "$USE_DENYLIST" -eq 1 ] && grep -a -i -q -E -f "$DENY_PATFILE" -- "$content"; then
    i=0
    while [ "$i" -lt "$DENY_N" ]; do
      grep -a -i -n -E -e "${DENY_PAT[$i]}" -- "$content" | cut -d: -f1 | while read -r k; do
        record "$name" "$k" "$map" "denylist:${DENY_LN[$i]}"
      done
      i=$((i + 1))
    done
  fi
  if [ "$skip_generic" -eq 0 ]; then
    if grep -a -q -E -f "$GEN_PATFILE" -- "$content"; then
      i=0
      while [ "$i" -lt "$GEN_N" ]; do
        grep -a -n -E -e "${GEN_PATS[$i]}" -- "$content" | cut -d: -f1 | while read -r k; do
          record "$name" "$k" "$map" "generic:${GEN_IDS[$i]}"
        done
        i=$((i + 1))
      done
    fi
    awk "$IPV4_AWK" "$content" | while read -r k; do
      record "$name" "$k" "$map" "generic:public-ipv4"
    done
  fi
}

# Paths are scanned as one pseudo-line reported as line 0.
scan_path() {
  printf '%s\n' "$1" > "$W/path.txt"
  printf '0\n' > "$W/path.map"
  scan_unit "$1" "$W/path.txt" "$W/path.map" 0
}

is_binary() {
  ! tr -d '\000' < "$1" | cmp -s - "$1"
}

# Reads a one-file `git diff -U0` on stdin; writes added lines to $1 and their new-file line
# numbers to $2. Anything before the first hunk header is diff metadata, not content.
extract_added() {
  : > "$1"
  : > "$2"
  awk -v C="$1" -v M="$2" '
    /^@@ / {
      inhunk = 1
      s = $0
      sub(/^@@ -[0-9]+(,[0-9]+)? \+/, "", s)
      ln = s + 0
      next
    }
    inhunk && /^\+/ { print substr($0, 2) > C; print ln > M; ln++ }
  '
}

scan_staged() {
  local f skip
  git diff --cached --name-only -z --diff-filter=ACMR | while IFS= read -r -d '' f; do
    scan_path "$f"
    skip=0
    [ "$f" = "$SELF_PATH" ] && skip=1
    git diff --cached -U0 --no-color --no-ext-diff -- "$f" | extract_added "$W/added.txt" "$W/added.map"
    [ -s "$W/added.txt" ] && scan_unit "$f" "$W/added.txt" "$W/added.map" "$skip"
  done
}

scan_all() {
  local f skip
  git ls-files -z --cached --others --exclude-standard | while IFS= read -r -d '' f; do
    [ -f "$f" ] || continue
    scan_path "$f"
    is_binary "$f" && continue
    skip=0
    [ "$f" = "$SELF_PATH" ] && skip=1
    scan_unit "$f" "$f" "" "$skip"
  done
}

if [ "$MODE" = "staged" ]; then scan_staged; else scan_all; fi

if [ -s "$HITS" ]; then
  awk '!seen[$0]++' "$HITS" | tee "$W/uniq" > /dev/stdout
  count=$(wc -l < "$W/uniq" | tr -d ' ')
  echo "leak_scan: $count hit(s) in $MODE scan; matched text is withheld on purpose" >&2
  exit 1
fi
exit 0
