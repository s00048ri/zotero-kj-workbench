"""``python -m zkj`` — start the workbench and open it."""

from __future__ import annotations

import threading
import webbrowser

import uvicorn

from .config import settings


def main() -> None:
    url = f"http://{settings.host}:{settings.port}/"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"{settings.app_name} → {url}")
    uvicorn.run("zkj.api.app:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
