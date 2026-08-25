# Zotero KJ Workbench

Turns your own Zotero highlights into research cards, lets Zotero hold the
grouping, and gives you a surface for writing what each grouping claims.

The specification this implementation follows is [docs/SPEC.md](docs/SPEC.md).
The validation spike it is based on is [docs/zkj_v0.py](docs/zkj_v0.py) —
reference implementation, not something to port line by line.

## Requirements

* Zotero desktop, running, with
  Settings → Advanced → “Allow other applications on this computer to
  communicate with Zotero” enabled. Without it every read is a 403.
* Zotero 10 or newer to write notes back. Older versions work read-only.
* Python 3.10+. This repo uses [uv](https://docs.astral.sh/uv/).

## Running

```
uv venv --python 3.12
uv pip install -e ".[dev]"
cd frontend && npm install && npm run build && cd ..
.venv/bin/python -m zkj          # opens http://127.0.0.1:8420/
```

The frontend builds into `src/zkj/api/web/dist`, which FastAPI serves from the
same port — one process, no CORS. Without a build the app still runs and serves
a diagnostic status page at `/status`.

While working on the interface, `npm run dev` in `frontend/` proxies `/api` to
port 8420, so the Python side keeps running unchanged.

## Tests

```
.venv/bin/python -m pytest
.venv/bin/ruff check src tests
```

No live Zotero is involved: the suite answers from `tests/fixtures/library.json`
through an httpx transport that imitates the local API, including the two
behaviours that matter most — reads are unpaginated, and `/items/<key>/children`
never returns annotations.

## Where things are

```
src/zkj/zotero/     the only code that talks to Zotero
  client.py         requests, capability detection, the annotation index
  models.py         typed views that keep the full payload in `raw`
  tree.py           the collection tree; subfolders are chapters
  reader.py         read one subtree, counting items rather than sightings
src/zkj/api/        FastAPI app; serves the interface on one port
src/zkj/store/      SQLite schema and numbered migrations
src/zkj/importer.py annotations and notes become cards, idempotently
src/zkj/locators.py where a passage is — and never an invented page
src/zkj/text.py     repairing extracted text without altering it
src/zkj/cards.py    filters, search, and the counts worth showing
src/zkj/writes.py   holding a Zotero write key inside Zotero's rules
src/zkj/materialize.py  cards into notes, and taking a batch back
src/zkj/annotate.py your own note on a passage, kept in step with Zotero
src/zkj/groups.py   the collections you filed cards into, and their labels
src/zkj/structure.py  your outline against your evidence
src/zkj/compose.py  question, claims, sections, and what each card does
src/zkj/prompts.py  the four blocks of text you paste into a chat
src/zkj/validate.py checking a draft against the evidence it was given
src/zkj/export.py   the paper as Markdown, with citekeys
frontend/           React + TypeScript; builds into src/zkj/api/web/dist
```

## Where the database lives

`~/Library/Application Support/zkj/zkj.sqlite3` on macOS, or wherever `ZKJ_DB`
points.

## What it writes into Zotero

Only when you ask, and never a highlighted passage:

* a standalone note for each card you choose, in `_KJ/Inbox` under your project
  collection — because a Zotero annotation cannot belong to a collection, and a
  note can, which is what lets you drag it into a group;
* the `_KJ` and `_KJ/Inbox` collections themselves, if they do not exist;
* a note for each group label, filed in the collection it names;
* your own comment on a highlight, if you write one — never over an existing
  comment without being asked twice.

Every batch of notes is recorded and can be taken back whole, from the Project
screen or straight after writing them.

## The loop

```
Zotero:    read, highlight, comment
Workbench: import → cards
Workbench: create notes in Zotero
Zotero:    drag notes into subcollections     ← the grouping happens HERE
Workbench: re-read → groups recovered
Workbench: write one proposition per group
Workbench: push labels back to Zotero
Workbench: structure comparison → cards worth re-reading
Workbench: build a prompt → paste into a chat → paste the draft back
Workbench: check it, then export Markdown with citekeys
```

Every step is re-runnable. Re-reading never destroys work.

## Milestones

| | | |
|---|---|---|
| M1 | Zotero adapter and status page | **done** |
| M2 | Import, cards, locators, Cards screen | **done** |
| M3 | Notes into Zotero | **done** |
| M4 | Placement read-back and Groups | **done** |
| M5 | Add my note | **done** |
| M6 | Structure comparison | **done** |
| M7 | Compose and prompt export | **done** |

## Writing

Nothing is sent anywhere. The app builds a complete block of text you paste
into a chat yourself, and you paste the draft back for checking. There are four
kinds: groups → themes, themes → questions, an outline, and a section draft.

A section prompt contains only the cards you assigned to that section, says
which are the source's words and which are your own, and instructs the model to
write `[EVIDENCE NEEDED: …]` rather than fill a gap.

A draft pasted back is checked for four things:

* citations of cards that were never in the section's evidence;
* quotations altered beyond spacing and quotation marks, with the alteration
  shown;
* **paraphrases that track the original's wording** — the risk with no
  quotation marks around it, and the one worth reading first;
* gaps the model was asked to leave open, listed as work.

Drafts are versioned and never overwritten. Markdown export emits Better
BibTeX-style citekeys — `[@smith2025, p. 132]` — so the file goes into pandoc
or Zotero without every citation being redone by hand.
