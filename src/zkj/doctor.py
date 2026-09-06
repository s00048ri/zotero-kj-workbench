"""``python -m zkj doctor`` — find out what will break before it does.

Everything this program does was written against a fixture. The parts that a
fixture cannot tell the truth about are all on this machine: whether Zotero is
answering, whether it will accept writes, whether an attachment's ``file://``
URL resolves to a file that is really there, and whether this filesystem makes
the links the NotebookLM staging folder is built from.

So this asks those questions directly and says what to do about each answer.
It reads; it writes nothing into Zotero.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import settings
from .store import connect
from .zotero import ZoteroClient, ZoteroError

# Enough attachments to catch a path this code cannot resolve, without making
# the check itself slow on a large library.
SAMPLE = 12


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    remedy: str | None = None
    # A check can pass and still be worth reading — a partial result, say.
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "remedy": self.remedy,
            "notes": self.notes,
        }


def run(client: ZoteroClient | None = None) -> list[Check]:
    client = client or ZoteroClient()
    checks = [_zotero(client)]
    if checks[0].ok:
        checks.append(_writes(client))
        checks.append(_collections(client))
    checks.append(_database())
    if checks[0].ok:
        checks.append(_attachment_files(client))
    checks.append(_staging())
    return checks


def _zotero(client: ZoteroClient) -> Check:
    try:
        info = client.server_info(refresh=True)
    except ZoteroError as e:
        return Check(
            "Zotero is answering",
            False,
            str(e),
            getattr(e, "remedy", None)
            or "Start Zotero and turn on Settings → Advanced → “Allow other "
            "applications on this computer to communicate with Zotero”.",
        )
    if not info.reachable:
        return Check(
            "Zotero is answering",
            False,
            f"No answer from {client.base}.",
            "Start Zotero and leave it running.",
        )
    return Check(
        "Zotero is answering",
        True,
        f"Zotero {info.zotero_version or '?'}, API version {info.api_version or '?'}.",
    )


def _writes(client: ZoteroClient) -> Check:
    info = client.server_info()
    if not info.writes_available:
        return Check(
            "Zotero will accept notes",
            False,
            "No Zotero-Server-ID header, so this Zotero is older than 10.",
            "Upgrade to Zotero 10 or newer. Everything else here still works "
            "read-only.",
        )
    return Check(
        "Zotero will accept notes",
        True,
        f"Server ID {info.server_id}. You will be asked to approve the first "
        "write — choose “Always Allow”.",
    )


def _collections(client: ZoteroClient) -> Check:
    try:
        collections = client.collections()
    except ZoteroError as e:
        return Check("The library reads", False, str(e), getattr(e, "remedy", None))
    if not collections:
        return Check(
            "The library reads",
            False,
            "Zotero answered, but reports no collections.",
            "Make a collection for the project and put its sources in it.",
        )
    return Check("The library reads", True, f"{len(collections)} collections.")


def _database() -> Check:
    path = Path(settings.db_path)
    try:
        conn = connect(path)
    except (sqlite3.Error, OSError) as e:
        return Check(
            "The workbench database",
            False,
            f"{path} could not be opened: {e}",
            "Point ZKJ_DB somewhere writable, or remove the file to start over.",
        )
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    projects = conn.execute("SELECT count(*) FROM project").fetchone()[0]
    notebooks = conn.execute("SELECT count(*) FROM notebook").fetchone()[0]
    conn.close()
    return Check(
        "The workbench database",
        True,
        f"{path} — schema {version}, {projects} project(s), {notebooks} notebook(s).",
    )


def _attachment_files(client: ZoteroClient) -> Check:
    """The check most likely to fail, and the one a fixture cannot make.

    Zotero hands back a percent-encoded ``file://`` URL. Turning that into a
    path that opens is where a non-ASCII filename, a space, or a drive letter
    goes wrong — and it goes wrong silently, as an attachment that simply
    cannot be carried into a notebook.
    """
    try:
        payloads = client.items(itemType="attachment")
    except ZoteroError as e:
        return Check("Attachment files resolve", False, str(e), getattr(e, "remedy", None))

    keys = [
        (p.get("data", p) or {}).get("key")
        for p in payloads
        if (p.get("data", p) or {}).get("linkMode") != "linked_url"
    ]
    keys = [k for k in keys if k][:SAMPLE]
    if not keys:
        return Check(
            "Attachment files resolve",
            True,
            "No stored attachments in this library to check.",
            notes=["Sources reachable by DOI or URL need no file at all."],
        )

    resolved, missing = [], []
    for key in keys:
        try:
            path = client.file_path(key)
        except ZoteroError:
            path = None
        (resolved if path is not None else missing).append((key, path))

    notes = []
    if resolved:
        # Printed verbatim, because a mangled non-ASCII name is only visible
        # when you look at it.
        notes.append(f"resolved, for example: {resolved[0][1]}")
    for key, _ in missing[:3]:
        url = None
        try:
            url = client.file_url(key)
        except ZoteroError:
            pass
        notes.append(
            f"{key}: Zotero says {url}" if url else f"{key}: Zotero reports no file"
        )

    if not resolved:
        return Check(
            "Attachment files resolve",
            False,
            f"None of {len(keys)} sampled attachments resolved to a file on disk.",
            "If Zotero reports a file:// URL above, this is a bug in how the "
            "workbench turns it into a path — send that line and it can be "
            "fixed. If it reports nothing, the files are not downloaded.",
            notes,
        )
    if missing:
        return Check(
            "Attachment files resolve",
            True,
            f"{len(resolved)} of {len(keys)} sampled attachments resolved.",
            "The rest are probably not downloaded, which is normal for a "
            "synced library. Files that do not resolve are simply left out of "
            "the staging folder.",
            notes,
        )
    return Check(
        "Attachment files resolve",
        True,
        f"All {len(resolved)} sampled attachments resolved.",
        notes=notes,
    )


def _staging() -> Check:
    """Whether the NotebookLM staging folder can be built here, and how.

    The folder is made of symlinks where the platform has them, because a
    library of PDFs copied is a library of PDFs twice. Windows without
    developer mode has no symlinks for an unprivileged process, and the code
    falls back to copying — worth knowing in advance rather than discovering
    it as a full disk.
    """
    root = Path(settings.db_path).parent / "notebooklm"
    probe = root / ".doctor"
    try:
        probe.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return Check(
            "The staging folder can be built",
            False,
            f"{root} is not writable: {e}",
            "Point ZKJ_DB at a writable directory.",
        )

    target, link = probe / "real.txt", probe / "link.txt"
    try:
        target.write_text("ok", encoding="utf-8")
        link.unlink(missing_ok=True)
        try:
            os.symlink(target, link)
            through = link.read_text(encoding="utf-8")
            how = "by symlink — the files are not duplicated"
        except (OSError, NotImplementedError):
            shutil.copy2(target, link)
            through = link.read_text(encoding="utf-8")
            how = (
                "by copying — this filesystem has no usable symlinks, so the "
                "staged folder costs as much disk as the files in it"
            )
        ok = through == "ok"
    except OSError as e:
        return Check(
            "The staging folder can be built",
            False,
            f"{root}: {e}",
            "Point ZKJ_DB at a writable directory.",
        )
    finally:
        shutil.rmtree(probe, ignore_errors=True)

    return Check(
        "The staging folder can be built",
        ok,
        f"{root}, {how}.",
        None if ok else "A staged file did not read back. Check the disk.",
        ["A browser's upload dialog has to follow these — that part is only "
         "provable by uploading one."],
    )


def report(checks: list[Check]) -> str:
    lines = []
    for check in checks:
        lines.append(f"{'PASS' if check.ok else 'FAIL'}  {check.name}")
        lines.append(f"      {check.detail}")
        for note in check.notes:
            lines.append(f"      · {note}")
        if check.remedy:
            lines.append(f"      → {check.remedy}")
        lines.append("")
    failed = [c for c in checks if not c.ok]
    lines.append(
        "Everything this machine can be asked about is in order."
        if not failed
        else f"{len(failed)} of {len(checks)} checks failed; see the arrows above."
    )
    return "\n".join(lines)
