#!/usr/bin/env bash
# Phase 0 verification — V1, and the parts of V2 that need no Google session.
#
# Run this on the machine that has Zotero, with Zotero running and
# Settings → Advanced → "Allow other applications…" on. It writes raw output
# to docs/phase0/v1-output.txt; paste that into docs/findings.md.
#
#   bash docs/phase0/verify.sh
#
# It only reads. Nothing is written to Zotero and nothing is sent anywhere.

set -uo pipefail
OUT="$(cd "$(dirname "$0")" && pwd)/v1-output.txt"
API="http://localhost:23119/api/users/0"
H='zotero-allowed-request: 1'

exec > >(tee "$OUT") 2>&1
echo "# Phase 0 / V1 — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "# $(uname -a)"
echo

echo "## V1.0 curl's own User-Agent (does not start with Mozilla/, so the"
echo "##      header should not be needed — recorded to prove which case is which)"
curl -s -o /dev/null -w 'no header:   %{http_code}\n' "$API/items/top?limit=1"
curl -s -o /dev/null -w 'with header: %{http_code}\n' -H "$H" "$API/items/top?limit=1"
echo

echo "## V1.0b a browser-like User-Agent, which is what a Chrome extension sends"
UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'
curl -s -o /dev/null -w 'Mozilla UA, no header:   %{http_code}\n' -A "$UA" "$API/items/top?limit=1"
curl -s -o /dev/null -w 'Mozilla UA, with header: %{http_code}\n' -A "$UA" -H "$H" "$API/items/top?limit=1"
echo "## and with an Origin header, which an extension cannot suppress"
curl -s -o /dev/null -w 'Origin, with header:     %{http_code}\n' \
  -A "$UA" -H "$H" -H 'Origin: chrome-extension://abcdefghijklmnopabcdefghijklmnop' \
  "$API/items/top?limit=1"
echo

echo "## V1.1 find one stored (not linked-url) attachment"
KEY=$(curl -s -H "$H" "$API/items?itemType=attachment&limit=50" \
  | python3 -c '
import json,sys
try:
    items = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for it in items:
    d = it.get("data", it)
    if d.get("linkMode") in ("imported_file", "imported_url"):
        print(d.get("key")); break
')
echo "attachment key: ${KEY:-<none found>}"
[ -z "${KEY:-}" ] && { echo "No stored attachment — cannot test the file endpoints."; exit 0; }
echo

echo "## V1.2 /file — the question the whole of Track B turns on."
echo "##      Bytes, or a redirect to file://?  (headers only, body suppressed)"
curl -s -o /dev/null -D - -H "$H" "$API/items/$KEY/file"
echo
echo "## V1.2b following the redirect, if there is one"
curl -s -o /dev/null -L -w 'final code: %{http_code}  type: %{content_type}  bytes: %{size_download}  url: %{url_effective}\n' \
  -H "$H" "$API/items/$KEY/file"
echo

echo "## V1.3 /file/view/url — known to work; recorded for completeness"
curl -s -H "$H" "$API/items/$KEY/file/view/url"; echo
echo

echo "## V1.4 the same three, with a browser User-Agent"
for path in "file" "file/view/url"; do
  curl -s -o /dev/null -w "$path (Mozilla UA): %{http_code}\n" -A "$UA" -H "$H" "$API/items/$KEY/$path"
done
echo
echo "# Written to $OUT"
