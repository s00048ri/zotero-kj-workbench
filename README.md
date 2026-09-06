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
src/zkj/summarise.py a machine summary of an attachment — never a card
src/zkj/notebooklm.py what goes into a notebook, and the link that comes back
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
  comment without being asked twice;
* a machine summary of an attachment, if you ask for one — as a child note of
  the source item, marked as generated on its first line and tagged
  `kj-summary`;
* a note carrying a NotebookLM notebook's link, on the item it is about, or in
  `_KJ` when the notebook covers the whole project;
* anything you paste back from a notebook, filed beside the evidence it read.

Everything in the last three is tagged `kj-generated`, and the importer
refuses any note carrying that tag. None of it can become a card, and a
summary or a report can never reach `_KJ/Inbox` — a child note cannot belong
to a collection at all, and the project-wide ones are filed one level up.

Every batch of notes is recorded and can be taken back whole, from the Project
screen or straight after writing them.

## NotebookLM

The NotebookLM screen is a bridge with you in the middle of it. NotebookLM has
no interface a program can use — see [docs/NOTEBOOKLM.md](docs/NOTEBOOKLM.md)
for what was checked and when — so the workbench does the parts around it:

1. **What to put in.** Your sources' DOIs and URLs as one block to paste into
   NotebookLM's bulk source box; the files with neither gathered into a single
   folder, named by citekey, to select all of; and — the one thing no crawler
   can fetch — **your own selected passages** as a single pasted text source.
2. **The address back.** You make the notebook and paste its address here. One
   per project, or one per source, or both.
3. **The link into Zotero.** That address is written into a note on the item
   itself, and into every card note created afterwards, so the notebook is one
   click from wherever you are reading.
4. **What came back.** A briefing doc, a study guide, a timeline, a mind map
   read out — paste it back and the workbench keeps it and can file it in
   Zotero, marked as NotebookLM's work.

Two pastes per notebook, and nothing that stops working when Google ships a
release. What you ask the notebook for is between you and it, as often as you
like.

## Summaries, and what they are not

The Sources screen will read a PDF and write a summary of it. That is for
deciding whether something is worth your attention — upstream of highlighting,
not a substitute for it. It is deliberately not one of the Steps, and three
things keep it out of the loop:

* a summary is stored in its own table, keyed to the attachment, where nothing
  in the card pipeline can reach it;
* the Zotero note it becomes hangs off the source item, which cannot be filed
  into a collection — so it can never land in `_KJ/Inbox`, where the grouping
  happens;
* the importer refuses any note tagged `kj-summary`, so a re-import cannot turn
  one into an idea card.

It needs the same Anthropic credentials as the Compose screen, and it is off
until those are there. Generating costs money and touches nothing; filing the
summaries in Zotero is a separate ask, and every batch can be taken back whole.

Why not NotebookLM, which is what this started as:
[docs/NOTEBOOKLM.md](docs/NOTEBOOKLM.md).

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
into a chat yourself, and you paste the draft back for checking.

**Nothing has to be specified first.** The shortest path is: highlight, sort
your notes into subcollections in Zotero, and build one prompt for the whole
paper. Your groups are what sets the paper going, not a set to be exhausted:
the argument, the sections and what each one claims are worked out from the
passages and marked as proposals, and passages that do not serve the argument
are left out with a reason.

Where the evidence does not carry a step the argument needs, the draft writes
it anyway in the paper's own voice and marks it `[UNSUPPORTED: …]`. What it may
never do is attribute it to anybody, or invent a source, an author, a date, a
page or a quotation. A draft that halts at every missing step is not a draft;
an unmarked assertion is not honest. Both are avoidable.

Three choices, all optional: draft it or report on what it can answer; quote
the sources, take only their ideas, or leave that per passage to the model. Anything you *have* decided — a research
question, a section, a label on a group — is carried through as yours and is
not the model's to revise. Specifying is how you take a decision back, never a
gate you pass before the tool will work.

Five kinds: the whole paper, groups → themes, themes → questions, an outline,
and a single section.

A section prompt contains only the cards you assigned to that section — or, if
you assigned none, every card, for the model to choose from. Either way it says
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

## Sending prompts to Claude, if you want to

Copying the prompt into a chat is the way this is meant to be used: it costs
nothing, needs no key, and lets you read exactly what is being sent before it
goes. Sending it from the app is optional and changes only who does the
pasting — the same prompt goes out, and what comes back is checked against the
same evidence by the same validator.

```
uv pip install -e ".[llm]"
ANTHROPIC_API_KEY=… .venv/bin/python -m zkj
```

or `ant auth login`, or paste a key into the Connect screen — that one is held
in memory for the run and is never written to the database, to a file, or to a
log.
