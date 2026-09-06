"""A summary of an attachment — machine-written, and never mistaken for yours.

This is the one place in the workbench where a machine reads the source
instead of the researcher. It exists because deciding whether to read a paper
is upstream of highlighting it, not a substitute for it. Everything here is
built around keeping those two apart:

* a summary is stored in its own table, keyed to the attachment, so nothing in
  the card pipeline can reach it;
* the Zotero note it becomes is a **child note of the source item**, which
  cannot belong to a collection — so it can never land in ``_KJ/Inbox``, where
  the grouping happens;
* it carries ``kj-summary``, and the importer refuses notes carrying it, so a
  re-import cannot turn a summary into an idea card;
* nothing is written to Zotero until the researcher asks, and every batch can
  be taken back whole, like every other write this app makes.

The provider is the same Claude path the compose screen uses. NotebookLM is
deliberately not it: see ``docs/NOTEBOOKLM.md`` for why a loop that must keep
working cannot be built on an interface with no version number.
"""

from __future__ import annotations

import base64
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import llm
from .cards import short_title
from .store import insert, now_iso
from .text import html_to_text
from .writes import WriteSession, parse_write_result
from .zotero import ZoteroClient, ZoteroError
from .zotero.client import WRITE_BATCH
from .zotero.notes import summary_note_payload

# The API refuses a request over 32 MB, and base64 costs a third on top of the
# file's own size. 20 MB of PDF leaves room for the prompt and the framing.
MAX_PDF_BYTES = 20 * 1024 * 1024
# A text attachment is sent as text, so the limit is the context window rather
# than the request size. This is generous and still bounded.
MAX_TEXT_CHARS = 600_000

PDF_TYPES = {"application/pdf"}
TEXT_TYPES = {
    "text/plain",
    "text/html",
    "text/markdown",
    "application/xhtml+xml",
}

PROMPT = """\
You are reading one source for a researcher who has not read it yet. They are \
deciding whether it is worth their attention, and if so, where in it to look. \
Write for that decision.

Give them, as plain prose paragraphs with no headings and no bullet points:

1. What this source is and what it sets out to do — one paragraph.
2. Its central claim, stated as the author would state it, and the argument or \
evidence it rests on — one or two paragraphs.
3. What is actually in it that a reader might want: the material, cases, data \
or sections that carry the weight, and roughly where they sit in the document.
4. What it does not do — its scope, and any limit the source itself admits to.

Rules you must not break:

* Describe only what is in the document. If something is not there, say so \
rather than supplying it from elsewhere.
* Do not quote more than a few words at a time. This summary is not evidence \
and must not be mistaken for the source's own text.
* Do not evaluate the work, recommend it, or say whether it is good.
* If the file is unreadable, or is not what its title claims, say that plainly \
and stop.

Aim for 300–500 words.
"""


class Unsummarisable(ValueError):
    """This attachment cannot be sent, and the message says why."""


@dataclass
class Attempt:
    attachment_key: str
    title: str
    ok: bool
    summary_id: str | None = None
    reason: str | None = None
    cost_usd: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "attachment_key": self.attachment_key,
            "title": self.title,
            "ok": self.ok,
            "summary_id": self.summary_id,
            "reason": self.reason,
            "cost_usd": round(self.cost_usd, 4),
        }


@dataclass
class SummariseResult:
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def written(self) -> int:
        return sum(1 for a in self.attempts if a.ok)

    @property
    def cost_usd(self) -> float:
        return sum(a.cost_usd for a in self.attempts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "summarised": self.written,
            "failed": len(self.attempts) - self.written,
            "cost_usd": round(self.cost_usd, 4),
            "attempts": [a.as_dict() for a in self.attempts],
        }


@dataclass
class NoteResult:
    batch_id: str | None = None
    created: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)
    dialogs_shown: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "created": self.created,
            "failures": self.failures,
            "dialogs_shown": self.dialogs_shown,
        }


# -- what can be sent -------------------------------------------------------


ATTACHMENT_SELECT = """
SELECT a.id                AS id,
       a.zotero_attachment_key AS attachment_key,
       a.content_type      AS content_type,
       a.title             AS title,
       a.filename          AS filename,
       a.link_mode         AS link_mode,
       s.id                AS source_id,
       s.zotero_item_key   AS source_key,
       s.title             AS source_title,
       s.creators_short    AS creators_short,
       s.year              AS source_year,
       m.id                AS summary_id,
       m.created_at        AS summarised_at,
       m.model             AS summary_model,
       m.zotero_note_key   AS summary_note_key
  FROM attachment a
  JOIN source s ON s.id = a.source_id
  LEFT JOIN summary m ON m.attachment_id = a.id
 WHERE s.project_id = ?
"""


def sendable(content_type: str | None) -> bool:
    return (content_type or "") in PDF_TYPES | TEXT_TYPES


def attachments(conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    """Every attachment in the project, and whether it has a summary yet."""
    rows = [
        dict(r)
        for r in conn.execute(ATTACHMENT_SELECT + " ORDER BY s.title, a.title", (project_id,))
    ]
    for row in rows:
        row["sendable"] = sendable(row["content_type"])
        row["citation"] = citation_of(row)
    return rows


def citation_of(row: dict[str, Any]) -> str:
    """“Smith 2025”, or the title when the item names no author."""
    author = row.get("creators_short")
    name = author or short_title(row.get("source_title"))
    if not name:
        return ""
    year = row.get("source_year")
    return f"{name} {year}" if author and year else ", ".join(filter(None, [name, year]))


def _one_attachment(
    conn: sqlite3.Connection, project_id: str, attachment_id: str
) -> dict[str, Any]:
    row = conn.execute(
        ATTACHMENT_SELECT + " AND a.id = ?", (project_id, attachment_id)
    ).fetchone()
    if row is None:
        raise Unsummarisable("No such attachment in this project.")
    return dict(row)


def content_blocks(row: dict[str, Any], path: Path) -> tuple[list[dict[str, Any]], bool]:
    """The message content for one attachment, and whether it was cut short.

    A PDF goes as a document block, which is what lets the model see the pages
    rather than a text extraction of them. Anything textual goes as text,
    because a text extraction is all there was to begin with.
    """
    content_type = row.get("content_type") or ""
    size = path.stat().st_size

    if content_type in PDF_TYPES:
        if size > MAX_PDF_BYTES:
            raise Unsummarisable(
                f"{size / 1_048_576:.0f} MB is past the {MAX_PDF_BYTES // 1_048_576} MB "
                "a single request can carry. Split the file, or summarise a chapter."
            )
        data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
        return (
            [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": data,
                    },
                },
                {"type": "text", "text": PROMPT},
            ],
            False,
        )

    if content_type in TEXT_TYPES:
        raw = path.read_text(encoding="utf-8", errors="replace")
        text = html_to_text(raw) if "html" in content_type or "xml" in content_type else raw
        text = text.strip()
        if not text:
            raise Unsummarisable("That file has no text in it.")
        truncated = len(text) > MAX_TEXT_CHARS
        if truncated:
            text = text[:MAX_TEXT_CHARS]
        return (
            [
                {"type": "text", "text": f"<document>\n{text}\n</document>"},
                {"type": "text", "text": PROMPT},
            ],
            truncated,
        )

    raise Unsummarisable(
        f"Nothing here can read {content_type or 'a file with no type'}. "
        "PDFs and text files can be summarised."
    )


# -- generating -------------------------------------------------------------


def summarise(
    conn: sqlite3.Connection,
    client: ZoteroClient,
    project: dict[str, Any],
    attachment_ids: list[str],
    *,
    effort: str = "medium",
    replace: bool = False,
) -> SummariseResult:
    """Read each attachment and keep what comes back. Zotero is not written.

    One failure does not stop the rest: a library has a broken file in it, and
    a batch that abandons ten good attachments over one bad one is useless.
    """
    result = SummariseResult()
    for attachment_id in attachment_ids:
        row = _one_attachment(conn, project["id"], attachment_id)
        attempt = Attempt(
            attachment_key=row["attachment_key"],
            title=row["title"] or row["filename"] or row["attachment_key"],
            ok=False,
        )
        try:
            summary_id, cost = _summarise_one(
                conn, client, project, row, effort=effort, replace=replace
            )
        except Unsummarisable as e:
            attempt.reason = str(e)
        except llm.LLMUnavailable:
            # Nothing about the next attachment would go any better.
            attempt.reason = "No way to send — check the credentials above."
            result.attempts.append(attempt)
            break
        except (RuntimeError, ZoteroError) as e:
            attempt.reason = str(e)
        else:
            attempt.ok = True
            attempt.summary_id = summary_id
            attempt.cost_usd = cost
        result.attempts.append(attempt)
    return result


def _summarise_one(
    conn: sqlite3.Connection,
    client: ZoteroClient,
    project: dict[str, Any],
    row: dict[str, Any],
    *,
    effort: str,
    replace: bool,
) -> tuple[str, float]:
    if row["summary_id"] and not replace:
        raise Unsummarisable(
            "There is already a summary of this attachment. Ask again with "
            "replace to overwrite it."
        )
    if not sendable(row["content_type"]):
        raise Unsummarisable(
            f"Nothing here can read {row['content_type'] or 'a file with no type'}. "
            "PDFs and text files can be summarised."
        )

    path = client.file_path(row["attachment_key"])
    if path is None:
        raise Unsummarisable(
            "Zotero has no file on disk for this attachment — it is a link, or "
            "the file was never downloaded."
        )

    blocks, truncated = content_blocks(row, path)
    answer = llm.send_content(blocks, effort=effort, max_tokens=8_000)
    if answer.refusal:
        raise Unsummarisable(answer.refusal)
    text = answer.text.strip()
    if not text:
        raise Unsummarisable("The answer came back empty.")

    values = {
        "created_at": now_iso(),
        "model": answer.model,
        "effort": effort,
        "text": text,
        "input_tokens": answer.input_tokens,
        "output_tokens": answer.output_tokens,
        "file_bytes": path.stat().st_size,
        "media_type": row["content_type"],
        "truncated": 1 if truncated else 0,
    }
    if row["summary_id"]:
        # Replacing means the old note no longer describes what is stored, so
        # the link to it is dropped. The note itself is the researcher's to
        # delete, or to overwrite by writing the new summary.
        conn.execute(
            "UPDATE summary SET "
            + ", ".join(f"{k} = ?" for k in values)
            + ", zotero_note_key = NULL, written_at = NULL WHERE id = ?",
            (*values.values(), row["summary_id"]),
        )
        return row["summary_id"], answer.cost

    summary_id = insert(
        conn,
        "summary",
        {
            "project_id": project["id"],
            "attachment_id": row["id"],
            "source_id": row["source_id"],
            **values,
        },
    )
    return summary_id, answer.cost


# -- putting them into Zotero ----------------------------------------------


def unwritten(conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM summary WHERE project_id = ? AND zotero_note_key IS NULL "
            "ORDER BY created_at",
            (project_id,),
        )
    ]


def summaries(conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT m.*, a.title AS attachment_title, a.zotero_attachment_key AS attachment_key,
               s.title AS source_title, s.creators_short AS creators_short,
               s.year AS source_year
          FROM summary m
          JOIN attachment a ON a.id = m.attachment_id
          JOIN source s ON s.id = m.source_id
         WHERE m.project_id = ?
         ORDER BY m.created_at DESC
        """,
        (project_id,),
    )
    out = []
    for row in rows:
        item = dict(row)
        item["citation"] = citation_of(item)
        out.append(item)
    return out


def write_notes(
    conn: sqlite3.Connection,
    client: ZoteroClient,
    session: WriteSession,
    project: dict[str, Any],
    *,
    summary_ids: list[str] | None = None,
) -> NoteResult:
    """Put summaries into Zotero as child notes of their source items."""
    rows = [r for r in summaries(conn, project["id"]) if not r["zotero_note_key"]]
    if summary_ids is not None:
        wanted = set(summary_ids)
        rows = [r for r in rows if r["id"] in wanted]

    result = NoteResult()
    if not rows:
        return result

    source_keys = {
        r["id"]: r["zotero_item_key"]
        for r in conn.execute(
            "SELECT id, zotero_item_key FROM source WHERE project_id = ?",
            (project["id"],),
        )
    }

    written_keys: list[str] = []
    for start in range(0, len(rows), WRITE_BATCH):
        batch = rows[start : start + WRITE_BATCH]
        payload = [
            summary_note_payload(
                row,
                project_name=project["name"],
                parent_item_key=source_keys[row["source_id"]],
                citation=row["citation"],
                attachment_title=row["attachment_title"],
            )
            for row in batch
        ]
        response = session.run(lambda key, p=payload: client.create_items(p, key))
        session.spend()
        ok, errors = parse_write_result(response, len(batch))

        for index, note_key in ok.items():
            conn.execute(
                "UPDATE summary SET zotero_note_key = ?, written_at = ? WHERE id = ?",
                (note_key, now_iso(), batch[index]["id"]),
            )
            written_keys.append(note_key)
            result.created += 1
        for index, message in errors.items():
            result.failures.append(
                {"summary_id": batch[index]["id"], "error": message}
            )

    if written_keys:
        result.batch_id = insert(
            conn,
            "write_batch",
            {
                "project_id": project["id"],
                "kind": "summaries",
                "created_at": now_iso(),
                "note_keys_json": json.dumps(written_keys),
                # A summary is not a card, and this batch owns no cards. The
                # revert path reads this as "nothing to unmark".
                "card_ids_json": json.dumps([]),
                "failures_json": json.dumps(result.failures, ensure_ascii=False),
            },
        )
    result.dialogs_shown = session.dialogs_shown
    return result


def forget(conn: sqlite3.Connection, project_id: str, summary_id: str) -> None:
    """Drop a summary from this app. The note in Zotero, if any, stays."""
    cursor = conn.execute(
        "DELETE FROM summary WHERE id = ? AND project_id = ?", (summary_id, project_id)
    )
    if cursor.rowcount == 0:
        raise Unsummarisable("No such summary.")
