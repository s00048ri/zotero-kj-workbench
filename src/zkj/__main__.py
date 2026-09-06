"""``python -m zkj`` — start the workbench and open it.

``python -m zkj doctor`` instead answers the questions a test suite cannot:
whether *this* Zotero is answering, and whether *this* machine's files and
filesystem behave the way the code assumes.
"""

from __future__ import annotations

import sys
import threading
import webbrowser

from .config import settings


def serve() -> None:
    import uvicorn

    url = f"http://{settings.host}:{settings.port}/"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"{settings.app_name} → {url}")
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
