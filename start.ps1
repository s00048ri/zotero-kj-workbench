# Start the workbench on Windows, and say plainly why if it will not start.
#
#   .\start.ps1
#
# "ERR_CONNECTION_REFUSED in the browser" has several causes and they look
# identical from there. This checks each one before starting, so the terminal
# says which it is instead of the browser saying nothing useful.

$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
$py   = Join-Path $root '.venv\Scripts\python.exe'
$port = 8420

Write-Host "Zotero KJ Workbench - starting from $root" -ForegroundColor Cyan
Write-Host ""

# 1. the interpreter
if (-not (Test-Path $py)) {
    Write-Host "NO VIRTUALENV" -ForegroundColor Red
    Write-Host "  $py does not exist."
    Write-Host "  Build it:"
    Write-Host "    py -3.13 -m venv .venv"
    Write-Host "    .\.venv\Scripts\python -m pip install -e ."
    exit 1
}
Write-Host ("python: " + (& $py -V 2>&1))

# 2. the package, inside that interpreter
& $py -c "import zkj" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "PACKAGE NOT INSTALLED IN THE VIRTUALENV" -ForegroundColor Red
    Write-Host "  The interpreter is there but 'import zkj' fails. Reinstall:"
    Write-Host "    .\.venv\Scripts\python -m pip install -e ."
    exit 1
}
Write-Host "package: zkj imports"

# 3. the port — an earlier run left behind is the commonest cause of all,
#    because the old process answers nothing useful and the new one dies.
$held = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($held) {
    $owner = Get-Process -Id $held[0].OwningProcess -ErrorAction SilentlyContinue
    Write-Host "PORT $port IS ALREADY IN USE" -ForegroundColor Red
    Write-Host ("  Held by PID " + $held[0].OwningProcess + " (" + $(if ($owner) { $owner.ProcessName } else { 'unknown' }) + ").")
    Write-Host "  That is probably this app still running in another window."
    Write-Host "  Either use that window, or stop it:"
    Write-Host ("    Stop-Process -Id " + $held[0].OwningProcess)
    exit 1
}
Write-Host "port:   $port is free"

# 4. the built interface. Not fatal — the app serves a diagnostic page
#    without it — but worth saying, because the blank-looking result is
#    otherwise mystifying.
if (-not (Test-Path (Join-Path $root 'src\zkj\api\web\dist\index.html'))) {
    Write-Host "interface: NOT BUILT - you will get the status page only." -ForegroundColor Yellow
    Write-Host "           cd frontend; npm install; npm run build; cd .."
} else {
    Write-Host "interface: built"
}

Write-Host ""
Write-Host "Starting. Leave this window open - closing it stops the server." -ForegroundColor Cyan
Write-Host "If the browser does not open by itself, go to http://127.0.0.1:$port/"
Write-Host ""

& $py -m zkj

# Reached only when the server exits. If that was immediate, the reason is
# above this line, and this says where to look.
Write-Host ""
Write-Host "The server has stopped. If that was immediate, the error is just above." -ForegroundColor Yellow
