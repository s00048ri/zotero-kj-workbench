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
* Python 3.10+ and Node 18+. [uv](https://docs.astral.sh/uv/) if you have
  it; plain `venv` and `pip` work just as well and need nothing installed.

## Running

**macOS and Linux**

```
python3 -m venv .venv            # or: uv venv --python 3.12
.venv/bin/python -m pip install -e .
cd frontend && npm install && npm run build && cd ..
.venv/bin/python -m zkj doctor   # check this machine first
.venv/bin/python -m zkj          # opens http://127.0.0.1:8420/
```

**Windows (PowerShell)**

Two things about PowerShell, both of which will bite before Python does.
Windows PowerShell 5.1 — the blue one, still the default — has no `&&`, so
these are separate lines rather than a chain. And a bare `.venv\Scripts\python`
is not a path to PowerShell: `Name\Command` is its module-qualified command
syntax, so it tries to load a module called `.venv` and fails with
`CouldNotAutoLoadModule`. The leading `.\` is what makes it a path.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -e .
cd frontend
npm install
npm run build
cd ..
.\.venv\Scripts\python -m zkj doctor
.\.venv\Scripts\python -m zkj
```

`py -3` takes the newest Python you have, and anything from 3.10 up is fine —
so do not ask for a version by number unless you have a reason to. If it
answers **No suitable Python runtime found**, the launcher has nothing to
give: `py -0` lists what it can see, and an empty list means Python is not
installed. Install it from [python.org](https://www.python.org/downloads/)
with **Add python.exe to PATH** ticked, then open a new PowerShell — the old
one keeps the old PATH.

Every line after the first depends on `.venv` existing. If the first line
fails, the rest fail with `CommandNotFoundException`, which is that failure
echoing rather than a new problem.

On Windows `doctor` will most likely report the staging folder as built *by
copying*: making a symbolic link needs Developer Mode or an elevated prompt,
and without one the workbench copies instead. That works — it just costs disk
for as long as a staged folder sits there.

`doctor` answers the questions the test suite cannot, because the suite runs
against a fixture and these are facts about your machine: whether Zotero is
answering, whether it is new enough to accept notes, whether your attachments'
`file://` URLs resolve to files that are really there, and whether this
filesystem makes the links the staging folder is built from. It reads only —
it writes nothing into Zotero. Each failure comes with what to do about it.

The frontend builds into `src/zkj/api/web/dist`, which FastAPI serves from the
same port — one process, no CORS. Without a build the app still runs and serves
a diagnostic status page at `/status`.

While working on the interface, `npm run dev` in `frontend/` proxies `/api` to
port 8420, so the Python side keeps running unchanged.

## Tests

The test tools are a dependency group rather than an extra, so they install on
their own line — `pip install -e ".[dev]"` will not find them.

```
.venv/bin/python -m pip install pytest ruff
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
src/zkj/notebooklm.py what goes into a notebook, and the link that comes back
src/zkj/doctor.py   what a fixture cannot answer: this Zotero, this disk
src/zkj/groups.py   the collections you filed cards into, and their labels
src/zkj/structure.py  your outline against your evidence
src/zkj/compose.py  question, claims, sections, and what each card does
src/zkj/prompts.py  the four blocks of text you paste into a chat
src/zkj/validate.py checking a draft against the evidence it was given
src/zkj/export.py   the paper as Markdown, with citekeys
frontend/           React + TypeScript; builds into src/zkj/api/web/dist
```

## Where the database lives

`~/Library/Application Support/zkj/zkj.sqlite3` on macOS,
`%APPDATA%\zkj\zkj.sqlite3` on Windows, `~/.local/share/zkj/zkj.sqlite3` on
Linux — or wherever `ZKJ_DB` points. `python -m zkj doctor` prints the path
it is actually using.

## What it writes into Zotero

Only when you ask, and never a highlighted passage:

* a standalone note for each card you choose, in `_KJ/Inbox` under your project
  collection — because a Zotero annotation cannot belong to a collection, and a
  note can, which is what lets you drag it into a group;
* the `_KJ` and `_KJ/Inbox` collections themselves, if they do not exist;
* a note for each group label, filed in the collection it names;
* your own comment on a highlight, if you write one — never over an existing
  comment without being asked twice;
* a note carrying a NotebookLM notebook's link, on the item it is about, or in
  `_KJ` when the notebook covers the whole project;
* anything you paste back from a notebook, filed beside the evidence it read.

The last two are tagged `kj-generated`, and the importer refuses any note
carrying that tag. Neither can become a card, and neither can reach
`_KJ/Inbox` — a note on an item cannot belong to a collection at all, and the
project-wide ones are filed one level up.

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

Nothing carried back from a notebook can become a card, and nothing in it is
evidence. That is what keeps this out of the loop below: a notebook is for
deciding what to read and what to ask, which is upstream of highlighting, not
a substitute for it.

## The first run, end to end

Everything below is done once, and after that the loop takes over.

1. **`python -m zkj doctor`.** Fix anything it flags before going further —
   most first-run trouble is one of the four things it checks.
2. **Start it**, and on the Projects screen point it at the Zotero collection
   your sources live in. It reads the subtree and turns your highlights into
   cards. Nothing is written back yet.
3. **Cards screen**: check that the passages are yours and that their page
   numbers look right. A locator marked estimated is a guess and says so.
4. **NotebookLM screen**, and pick a scope — the whole project to start with.
   Copy the three blocks in order: your passages, then the sources reachable
   by link, then, if there are files with neither, press *Gather them into one
   folder* and upload from there.
5. **Make the notebook** at notebook.google.com, paste those in, and copy its
   address back into the *Address* field here.
6. **Write the links into Zotero.** Zotero asks for permission the first time;
   choose **Always Allow**, or it asks again for every batch and only allows
   five dialogs a minute. Then look at the item in Zotero — the note should be
   there with a link you can click.
7. **Ask the notebook for something** — a briefing doc, a study guide, a
   timeline. Paste anything worth keeping back on this screen, and file it in
   Zotero if you want it beside the evidence.

If step 6 or 7 wrote something you did not want, the Project screen lists
every batch this tool has written and takes any of them back whole.

## Two machines, one library

Zotero syncs; this workbench does not. Its database is per-machine
(`%APPDATA%\zkj` on Windows, `~/Library/Application Support/zkj` on macOS), so
each machine imports its own cards from the same synced library and keeps its
own record of what it wrote. That is deliberate — the two Zotero databases have
different server IDs, and a project imported from one refuses to be written
into the other rather than attaching cards to the wrong library.

What follows from it:

* **Import on each machine.** Cards, groups and labels are read back out of
  Zotero, which is the shared part, so the second machine reconstructs them
  rather than needing them copied.
* **Register the notebook on each machine.** The notebook's address is not
  synced. Paste it into the NotebookLM screen on both.
* **The link note is not written twice.** Before writing, the workbench looks
  for a `kj-notebook` note on the same item carrying the same URL. A note the
  other machine wrote has already synced, so it is adopted instead — the
  screen says how many.
* **Notes from the other machine are not misread.** A card note that this
  database has no card for is counted as an unknown note and left alone, never
  turned into a new card.

Pasted-back reports are per-machine and are not adopted: two machines pasting
the same briefing doc means two notes, because a report is something you chose
to keep rather than something the tool wrote.

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
