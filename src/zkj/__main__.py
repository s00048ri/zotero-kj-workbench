"""``python -m zkj`` — start the workbench and open it.

``python -m zkj doctor`` instead answers the questions a test suite cannot:
whether *this* Zotero is answering, and whether *this* machine's files and
filesystem behave the way the code assumes.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
import webbrowser

from .config import settings


# Long enough for a cold start on a slow machine — importing the app pulls in
# scikit-learn and numpy, which is seconds rather than milliseconds.
STARTUP_WAIT = 90.0


def _open_when_listening(url: str, host: str, port: int) -> None:
    """Open the browser once the port answers, and not a moment before.

    Opening on a timer showed the researcher ERR_CONNECTION_REFUSED whenever
    the server took longer to start than the timer waited — which on a cold
    Windows start it reliably did, because the browser was ahead of the
    imports rather than the server being broken. So this asks the port
    instead of guessing.
    """
    deadline = time.monotonic() + STARTUP_WAIT
    target = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((target, port), timeout=0.5):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.25)
    print(
        f"The server has not answered on port {port} after "
        f"{STARTUP_WAIT:.0f} seconds. Whatever it printed above says why; "
        f"the browser was not opened."
    )


def serve() -> None:
    import uvicorn

    url = f"http://{settings.host}:{settings.port}/"
    threading.Thread(
        target=_open_when_listening,
        args=(url, settings.host, settings.port),
        daemon=True,
    ).start()
    print(f"{settings.app_name} → {url}")
    print("Starting — the browser opens by itself once it is ready.")
    uvicorn.run("zkj.api.app:app", host=settings.host, port=settings.port)


def doctor() -> int:
    from .doctor import report, run

    checks = run()
    print(report(checks))
    return 0 if all(c.ok for c in checks) else 1


def main() -> None:
    argument = sys.argv[1] if len(sys.argv) > 1 else ""
    if argument == "doctor":
        raise SystemExit(doctor())
    if argument in ("-h", "--help", "help"):
        print(
            f"{settings.app_name}\n\n"
            "  python -m zkj           start the workbench and open it\n"
            "  python -m zkj doctor    check this machine before running it\n"
        )
        raise SystemExit(0)
    if argument:
        print(f"Unknown argument {argument!r}. Try: python -m zkj --help")
        raise SystemExit(2)
    serve()


if __name__ == "__main__":
    main()
