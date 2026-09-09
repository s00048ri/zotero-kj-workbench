# Phase 0 verification (V1) for Windows PowerShell.
#
# The same checks as verify.sh. It shells out to curl.exe — the real curl that
# ships with Windows 10 and 11 — rather than Invoke-WebRequest, which follows
# redirects on its own and throws on a non-2xx status. Seeing the raw 302 is
# the entire point of V1, so the tool that shows it is the one to use.
#
# In PowerShell `curl` is an alias for Invoke-WebRequest, so every call below
# says curl.exe deliberately.
#
# Run with Zotero running and the local API allowed:
#
#   .\docs\phase0\verify.ps1
#
# It only reads. Nothing is written to Zotero and nothing is sent anywhere.

$ErrorActionPreference = 'Continue'
$out = Join-Path $PSScriptRoot 'v1-output.txt'
$api = 'http://localhost:23119/api/users/0'
$hdr = 'zotero-allowed-request: 1'
$ua  = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'

function Say($text) { $text | Tee-Object -FilePath $out -Append }

if (Test-Path $out) { Remove-Item $out }
Say "# Phase 0 / V1 - $(Get-Date -Format o)"
Say "# $([System.Environment]::OSVersion.VersionString) / PowerShell $($PSVersionTable.PSVersion)"
Say ""

Say "## V1.0 curl's own User-Agent (not Mozilla/, so the header should not be needed)"
Say (curl.exe -s -o NUL -w 'no header:   %{http_code}\n' "$api/items/top?limit=1")
Say (curl.exe -s -o NUL -w 'with header: %{http_code}\n' -H $hdr "$api/items/top?limit=1")
Say ""

Say "## V1.0b a browser-like User-Agent, which is what a Chrome extension sends"
Say (curl.exe -s -o NUL -w 'Mozilla UA, no header:   %{http_code}\n' -A $ua "$api/items/top?limit=1")
Say (curl.exe -s -o NUL -w 'Mozilla UA, with header: %{http_code}\n' -A $ua -H $hdr "$api/items/top?limit=1")
Say "## and with an Origin header, which an extension cannot suppress"
Say (curl.exe -s -o NUL -w 'Origin, with header:     %{http_code}\n' `
       -A $ua -H $hdr -H 'Origin: chrome-extension://abcdefghijklmnopabcdefghijklmnop' `
       "$api/items/top?limit=1")
Say ""

Say "## V1.1 find one stored (not linked-url) attachment"
$key = $null
try {
    $items = curl.exe -s -H $hdr "$api/items?itemType=attachment&limit=50" | ConvertFrom-Json
    $key = ($items | Where-Object { $_.data.linkMode -eq 'imported_file' -or $_.data.linkMode -eq 'imported_url' } |
            Select-Object -First 1).data.key
} catch { }
Say "attachment key: $(if ($key) { $key } else { '<none found>' })"
if (-not $key) {
    Say "No stored attachment - cannot test the file endpoints."
    Say "# Written to $out"
    exit 0
}
Say ""

Say "## V1.2 /file - the question the whole of Track B turns on."
Say "##      Bytes, or a redirect to file://?  (headers only, body suppressed)"
Say (curl.exe -s -o NUL -D - -H $hdr "$api/items/$key/file")
Say ""
Say "## V1.2b following the redirect, if there is one"
Say (curl.exe -s -o NUL -L -w 'final code: %{http_code}  type: %{content_type}  bytes: %{size_download}  url: %{url_effective}\n' `
       -H $hdr "$api/items/$key/file")
Say ""

Say "## V1.3 /file/view/url - known to work; recorded for completeness"
Say (curl.exe -s -H $hdr "$api/items/$key/file/view/url")
Say ""

Say "## V1.4 the same two, with a browser User-Agent"
foreach ($path in @('file', 'file/view/url')) {
    Say (curl.exe -s -o NUL -w ($path + ' (Mozilla UA): %{http_code}\n') -A $ua -H $hdr "$api/items/$key/$path")
}
Say ""
Say "# Written to $out"
