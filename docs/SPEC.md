# Zotero KJ Workbench — Implementation Brief

**Audience:** Claude Code
**Status:** v1 spec, superseding an earlier untested draft
**Basis:** a working command-line spike (`zkj_v0.py`) that ran end-to-end against a real
Zotero 10 library. Every claim in §1 was verified there, not assumed.

Read §1 before designing anything. It removes about half of what the earlier draft
proposed to build.

---

## 0. What this product is

A researcher highlights passages while reading in Zotero. Those highlights, the groupings
the researcher makes, and the propositions the researcher writes about each grouping are
the raw material of a paper. This tool turns the first into cards, lets Zotero hold the
second, and gives the researcher a decent surface for the third.

The thesis: **the value is in the researcher's own selections and connections.** A general
AI can summarise a literature it has read. It cannot tell you which twelve passages you
found arresting, or why you put these three together. This tool's job is to capture that
and keep it attached to its evidence.

Writing is downstream and comes last. Do not optimise for producing prose quickly.

---

## 1. Findings from the spike — read this first

### 1.1 Zotero's local API behaves in specific ways

Base URL `http://localhost:23119/api`, user prefix `/users/0`. Requires
Settings → Advanced → "Allow other applications on this computer to communicate with
Zotero"; returns `403` otherwise. Never touch Zotero's SQLite file.

**Annotations are not returned by `/items/<attachmentKey>/children`.** This is the single
most important finding. That endpoint returns child *notes* but no annotations, so
per-attachment lookups come back empty and an import silently yields zero cards. Instead:

```
GET /users/0/items?itemType=annotation&limit=0
```

returns every annotation in the library, each carrying `parentItem`. Build an index keyed
by attachment key **once per import**. This is also 1 request instead of N.

**Reads are unpaginated by default** — a whole collection arrives in one response. No
cursor logic needed.

**Writes require Zotero 10+** (released 2026-08-17). Detect by the presence of the
`Zotero-Server-ID` response header; absent means an older Zotero and the app must run
read-only with all write features disabled rather than failing.

**Write authorization has semantics that break naive batch code:**

```
POST /api/local/authorize
Zotero-Server-ID: <id>
{"appName": "Zotero KJ Workbench"}
→ {"key": "<32 chars>", "remember": false}
```

- `remember: false` (user pressed "Allow") means the key is **consumed by the first
  successful write**. The next write needs a new dialog.
- Zotero accepts at most **five dialog-showing requests per minute**; beyond that, `429`
  with `Retry-After`.
- Therefore: the UI must actively steer the user to **"Always Allow"**, must handle `401`
  by re-authorizing and retrying once, and must pace itself when it does not have a
  remembered key. A naive implementation that authorizes per item would deadlock on the
  sixth card.
- Every write also needs `Zotero-Server-ID` (else `428`) and `Zotero-API-Key: <key>`.
- Batch writes at **50 objects per request**. Parse the multi-object response shape
  (`success` / `successful` / `failed`) — a partial failure is normal and must be surfaced
  per card, not as a blanket error.

**Object versions are local to that Zotero database** and unrelated to web API versions.
Partition all stored state by `Zotero-Server-ID` and refuse to write when the running
Zotero's ID differs from the one a project was imported from.

### 1.2 Annotations cannot be filed; notes can

A Zotero annotation is a child of an attachment. **It cannot belong to a collection.** To
make a highlight into something the researcher can move around, it must be materialised as
a **standalone note**. This is not an optional convenience feature — it is the mechanism
that makes the whole product work.

### 1.3 Zotero itself is the grouping surface — do not rebuild it

The earlier draft specified a three-column KJ board with `dnd-kit` drag-and-drop and a
whole `Cluster` table with its own CRUD API. **The spike showed this is unnecessary.**

Once cards are standalone notes, the researcher drags them into subcollections *in Zotero*,
using an interface they already know, that already has search, tags, colours, and undo. A
re-import reads `data.collections` on each note and recovers the grouping exactly.

Verified round trip: 17 notes created → dragged into three subcollections in Zotero →
re-import read back 9 / 5 / 3.

**Do not build a card board. Do not build drag-and-drop. Delete the `Cluster` entity.**
A collection under `_KJ` *is* a group. This removes an entire milestone from the earlier
plan.

### 1.4 Highlights are usable as cards; comments are the scarce input

Measured on a real collection: median 234 characters, p90 376, 6% over 400. Highlights are
already card-sized. The worry that they would be unusable 600-character passages was wrong.

What was missing was the researcher's own writing: **17 highlights, 0 comments.** The
product must therefore treat authoring the researcher's own ideas as a first-class,
prompted activity — not as an optional field that happens to be empty.

### 1.5 Grouping first, labelling after, works

The spike's final loop — group the cards in Zotero, then write one proposition per group —
produced usable output where demanding comments up front had produced nothing. Labels are
written *after* grouping, and the act of writing one is where the thinking happens. Build
for this order.

### 1.6 Assorted data facts

- EPUBs have no pages. `annotationPageLabel` is populated only when the book ships an
  EPUB 3 page-list. Otherwise the position is an `epubcfi(...)`.
- PDF `annotationPageLabel` is the *displayed* label (can be roman numerals, can be absent)
  and is distinct from `position.pageIndex`.
- Extracted highlight text carries end-of-line hyphenation, ligatures, and soft hyphens.
- Some Zotero items have no `date`, so a citation renders author-only.
- A note in two collections is encountered once per collection during traversal. Count
  cards, not sightings.

---

## 2. Architecture decision: local web app, not a Zotero plugin

The request was "a Zotero plugin if possible, otherwise browser-based." **Build the
browser-based version.** The reasoning, so it can be revisited later:

Zotero plugins are bootstrapped Firefox-style extensions. They run in a sandbox without
ordinary globals, are written against XPCOM and Zotero's internal JS API, and the dev loop
involves restarting Zotero with `-purgecaches`. Every plugin needed rewriting for Zotero 7,
and Zotero now ships a feature release every 6–10 weeks, so a plugin is a standing
maintenance commitment against a moving internal API.

Against that: the local API gives everything this product needs from outside the app, on a
documented, versioned interface, and the spike already proved it. A web UI can be built and
iterated in minutes.

The cost of the browser approach is the alt-tab between the app and Zotero. That cost is
low because §1.3 means the Zotero side of the loop is *supposed* to happen in Zotero.

Keep all Zotero access behind one adapter module so a plugin front-end remains possible
later. Do not scatter `fetch` calls through the UI.

### Stack

```
Backend    Python 3.10+, FastAPI, SQLite, httpx, Pydantic
Frontend   React + TypeScript + Vite, TanStack Query
Packaging  one process: FastAPI serves the built static frontend on one port
```

One process, one port, one command to start. No CORS, no dual dev servers in production.
`python -m zkj` opens the browser at `localhost:8420`.

**Do not add:** a vector database, a task queue, Docker, user accounts, or a cloud
component. Single user, one machine.

**Do not port `zkj_v0.py` line by line**, but do read it. Its `ZoteroClient`,
`resolve_locator`, `normalise_quote`, `build_annotation_index`, `_record_placement`, and
`WriteSession` classes encode the findings in §1 and are the reference implementations.

---

## 3. Data model

UUID primary keys. SQLite. Constraints below are corrections of real bugs found in the
earlier draft — implement them exactly.

```
Project
  id, name, zotero_server_id, root_collection_key,
  kj_root_key, kj_inbox_key,
  research_question, created_at, last_import_at
  UNIQUE (name)
  -- refuse to re-point an existing project at a different root collection

Collection                       -- mirror of the Zotero tree under the root
  id, project_id, zotero_collection_key, parent_key, name, path, depth
  UNIQUE (project_id, zotero_collection_key)

Source
  id, project_id, zotero_item_key, item_type, title, creators_json,
  creators_short, year, publication_title, doi, isbn, url, raw_json
  UNIQUE (project_id, zotero_item_key)      -- per project, NOT global

SourceCollection                 -- many-to-many; the researcher's prior structure
  source_id, collection_id

Attachment
  id, source_id, zotero_attachment_key, content_type, title, filename,
  link_mode, raw_json
  UNIQUE (source_id, zotero_attachment_key) -- the earlier draft had no constraint

Annotation
  id, attachment_id, zotero_annotation_key, annotation_type,
  text_raw, comment_raw, color, page_label, sort_index,
  position_json, date_modified, raw_json, content_hash
  UNIQUE (attachment_id, zotero_annotation_key)
  -- NOT globally unique on the key: the same source in two projects must not collide
  -- content_hash covers text, comment, colour, page label, sort index AND position

Card
  id, project_id, human_id ("KJ-0042"), origin_key,
  kind          ENUM(quote, idea, image)
  origin        ENUM(annotation_text, annotation_comment, child_note,
                     standalone_note, group_label, manual)
  text, text_raw, human_label,
  source_id, annotation_id,
  zotero_note_key    -- the standalone note THIS TOOL created
  origin_note_key    -- the Zotero note the card's text came from
  parent_card_id     -- an idea card points at the quote it responds to
  prior_collection_id, prior_path, prior_ambiguous,
  locator_type, locator_value, locator_source, locator_estimated,
  locator_detail_json,
  kj_collection_keys_json, kj_path, materialized_at,
  color, status, content_hash, created_at, updated_at
  UNIQUE (project_id, origin_key)           -- makes re-import idempotent by construction
```

`zotero_note_key` and `origin_note_key` **must** stay separate. Conflating them makes cards
derived from Zotero notes look already-materialised and silently excludes them. This was a
real bug in the spike.

`origin_key` is deterministic: `annotation:<key>:quote`, `annotation:<key>:idea`,
`note:<key>`, `group:<collection path>`. Idempotency falls out of the unique constraint;
do not implement it as procedural checks.

Then the argument layer, unchanged in shape from the earlier draft:

```
ResearchQuestion  id, project_id, text, rationale, status, origin
Claim             id, project_id, research_question_id, text, claim_type,
                  parent_claim_id, sort_order
OutlineSection    id, project_id, parent_section_id, title, purpose, sort_order
SectionCardUsage  id, section_id, card_id, include,
                  citation_mode ENUM(direct_quote, paraphrase, reference_only),
                  argument_role ENUM(evidence, counterevidence, background,
                                     definition, method, example),
                  user_instruction
PromptExport      id, project_id, kind, content, created_at
```

`SectionCardUsage` matters: one card can appear in several sections with different roles.

---

## 4. Card kinds — and why ideas are first-class

| kind | origin | whose words | where it comes from |
|---|---|---|---|
| `quote` | `annotation_text` | the source's | a highlight |
| `idea` | `annotation_comment` | **the researcher's** | a comment typed on a highlight |
| `idea` | `child_note` | **the researcher's** | a note attached to an item |
| `idea` | `standalone_note` | **the researcher's** | a note filed straight into a collection |
| `idea` | `group_label` | **the researcher's** | the proposition written about a group |
| `image` | `annotation_text` | — | placeholder, excluded from analysis |

A highlight with a comment produces **two cards**, linked by `parent_card_id`. Never fold
the comment into the quote card as a subtitle. The pair — *what the source said* + *what I
take it to mean* — is the atom of an argument, and it is exactly what a general-purpose AI
cannot supply.

Quote cards keep `text_raw` verbatim and `text` cleaned. Cleaning repairs end-of-line
hyphenation, expands ligatures, removes soft hyphens, collapses whitespace. **Do not apply
NFKC** — it rewrites full-width characters and would silently alter Japanese quotations.
Use NFC. AI output must never write to `text` or `text_raw`.

Materialisation rules:

- `standalone_note` cards are already filable in Zotero → no new note; set both key fields.
- `child_note`, `annotation_text`, `annotation_comment`, `group_label` → need a standalone
  note created.
- `group_label` notes are filed **into the collection they name**, not into Inbox.
  Everything else starts in `_KJ/Inbox`.

---

## 5. Locators — never invent a page number

```
locator_type   ENUM(page, chapter, cfi, none)
locator_value  TEXT
locator_source ENUM(page_label, page_index, epub_page_list, epub_spine, cfi, none)
locator_estimated BOOLEAN
```

**PDF:** `annotationPageLabel` → `position.pageIndex + 1` → none. Record which was used;
a displayed label and a zero-based index are different claims.

**EPUB:**
1. `annotationPageLabel` present (book has an EPUB 3 page-list) → real `page`.
2. Otherwise parse the CFI to a spine index, open the EPUB from disk via
   `GET /users/0/items/<key>/file/view/url` (returns a `file://` URL as plain text), read
   the OPF spine, and use the **chapter** as the locator. CSL supports a chapter locator,
   so this is properly citable — and honest, which a fabricated page is not.
3. Optionally, with the feature explicitly enabled, look up the print page count from
   Google Books and store an `estimated_page` computed from the card's character-offset
   fraction through the book. Flag `locator_estimated` and render it as "p. 132 (est.)".

Google Books page counts are frequently the ebook's own pagination. An estimate is a hint
for finding the passage again, never a citation. The UI must show estimated locators in a
visually distinct way and warn before export.

---

## 6. Screens

Six screens. The design brief is at §11.

### 6.1 Connect

Zotero reachable / API version / server ID / write capability. If no `Zotero-Server-ID`,
say plainly: writes need Zotero 10 or newer, everything else works. If `403`, give the
exact settings path. One button: **Authorize writes** — and the dialog copy must tell the
user to choose "Always Allow" and say why (otherwise every batch prompts again).

### 6.2 Project

Zotero collection tree on the left. Selecting a collection previews counts: sources,
attachments, annotations, existing cards. **Create project.**

Refuse, with an explanation, to point an existing project at a different root collection.
Mixing two collections into one project corrupts the structure comparison.

### 6.3 Cards

The reading surface. This is where the researcher spends real time, so it has to be good
for reading, not just for listing.

- Filters: source, year, highlight colour, kind, has-comment, locator type, group, and
  full-text search.
- Each card shows its text at a comfortable measure (~65 characters), citation, locator
  (estimated ones marked), and its linked idea card if there is one.
- **Add my note** on every quote card. This is the most important control in the product,
  because §1.4 says this input is what's scarce. One text field, saves as an idea card
  linked to the quote, and — where the card came from an annotation — writes it back to
  Zotero as the annotation's comment so the two stores agree.
- A visible, non-nagging counter: "17 quotes · 3 of them have your note on them."
- Bulk select → **Create notes in Zotero**, with a live count and the batching/authorization
  behaviour of §1.1 handled invisibly except for a progress line.

### 6.4 Groups

The labelling surface, and the heart of the product.

For each collection the researcher has filed cards into: the group's name, its member cards
in full, and a **label** field — one sentence, a proposition rather than a topic. Placeholder
copy must teach the distinction: *"Competition" is a heading. "The competition frame borrows
its urgency from security language" is a label.*

Two aids, both computable without any AI:

- **Least alike in this group** — the member pair with the lowest TF-IDF cosine similarity
  (character n-grams, so it works on Japanese and French as well as English). If two cards
  share little vocabulary yet you grouped them, the reason you did is what the label needs
  to say.
- **Ungrouped** — cards still sitting in Inbox, as a persistent count with a link.

Saving a label creates a `group_label` idea card. **Push labels to Zotero** files each one
as a note inside the collection it names, so in Zotero the group and its proposition sit
together.

Labels are editable forever; re-saving updates the same card and the same note.

### 6.5 Structure

The one analysis no other tool can do: **your outline versus your evidence.**

Your Zotero subfolders are your chapters. TF-IDF over character n-grams → Ward clustering on
L2-normalised vectors, *k* defaulting to your folder count. (Average linkage was tried and
collapses into one giant cluster on sparse text; Ward scored materially better on the same
data. Use Ward.)

Show:
- **Adjusted Rand Index** and **NMI** against the researcher's folders, with plain-language
  framing: 0.0 means your folders and the text agree no more than chance would.
- A contingency heatmap, folders × clusters.
- The three cards nearest each cluster centroid, so a cluster is interpretable without asking
  a model to name it.
- **The misfit list** — cards whose text sits with a different chapter than the one they were
  filed under. This is the actual output; everything else is context for reading it.

Frame misfits as *cards worth re-reading*, never as a proposed reorganisation. This is
bag-of-character-n-grams: it sees vocabulary, not argument. A card can be lexically identical
to chapter 3 and belong in chapter 5.

Warn when one cluster holds more than 70% of cards — the texts are too uniform for the method
and the numbers are unreliable.

These scores are also the **baseline** for any future AI clustering. If embeddings plus a
model cannot beat TF-IDF plus Ward, the AI is not earning its cost.

### 6.6 Compose

Research question, claims, outline sections, and evidence assignment. Drag is unnecessary
here too — a card's section assignment is a select, and its `citation_mode` and
`argument_role` are selects.

This screen's output is §7.

---

## 7. Prompt export — the deliverable for now

No API key, no LLM calls, no cost. The app assembles a **complete, self-contained block of
text** that the researcher pastes into Claude Chat. This is a real feature with real design
work, not a debug dump.

### Behaviour

- A **Copy** button that actually copies (Clipboard API, with a `<textarea>` fallback), plus
  **Download .md**.
- A live character count and an estimated token count, with a soft warning past ~150k
  characters and an offer to export section by section instead of whole-paper.
- The exact text is stored as a `PromptExport` row so the researcher can see later which
  bundle produced which draft.

### Four export kinds

**1. Groups → themes and tensions** (available as soon as groups exist)
Every group with its label and member cards. Asks for: what each group is really claiming,
which groups are in tension, what is conspicuously absent.

**2. Themes → research questions**
Group labels plus contradictions, asking for 3–5 candidate questions, each naming the groups
that support it. Must instruct: *a gap in this collection is not a gap in the literature.*

**3. Outline**
Selected question plus claims plus group labels → a section structure.

**4. Section draft** — the one with hard requirements

```
=== TASK ===
Draft the section named below using ONLY the evidence listed in ALLOWED EVIDENCE.

Rules:
- Every source-dependent claim carries a marker: [[CITE:KJ-0042]]
- Never invent a source, author, date, page number, or quotation.
- Where citation_mode is direct_quote, reproduce the quotation exactly as given.
- Where citation_mode is paraphrase, restate it in your own words — do not
  track the original's wording or sentence shape.
- If the evidence does not support something the section needs, write
  [EVIDENCE NEEDED: what is missing] rather than filling the gap.
- Distinguish what a source states, what the researcher takes it to mean,
  and what this paper argues.

=== SECTION ===
Title:   Institutional capacity
Purpose: ...
Research question: ...
Thesis: ...
Target length: 1200 words

=== ALLOWED EVIDENCE ===
[KJ-0042] quote | direct_quote | evidence
  Smith 2025, p. 132
  "Human oversight becomes increasingly difficult as autonomous agents
   operate across organizational boundaries."

[KJ-0043] idea | — | my reading of KJ-0042
  (researcher's own words)
  Oversight is being treated as an individual capability when it is an
  organisational one.

[KJ-0051] quote | paraphrase | counterevidence
  Tanaka 2024, ch. "Regulatory design" (locator estimated — verify)
  ...
```

Idea cards are included and **labelled as the researcher's own**, never as citable sources.
The distinction has to survive into the pasted text, because that is the only place the
model will see it.

### Paste-back and validation

A **Paste draft back** box. On paste, the app:

1. extracts every `[[CITE:...]]` marker and checks each ID against the section's evidence
   whitelist, listing any unknown ID;
2. for every `direct_quote` card cited, checks the quoted string against `text_raw` after
   whitespace and typographic-quote normalisation only, and flags any mismatch with a diff;
3. for every `paraphrase` card, computes n-gram overlap against the original and flags
   anything too close — **this is the plagiarism risk the earlier draft had no check for at
   all, and it is higher than the direct-quote risk because there are no quotation marks**;
4. lists `[EVIDENCE NEEDED]` markers as open work;
5. renders markers as `(Smith, 2025, p. 132)` for reading while keeping the marker in the
   stored text;
6. reports coverage: which assigned cards went unused.

Drafts are append-only and versioned. Never overwrite.

### Export for writing

Markdown, with citations as **Better BibTeX-style keys** (`[@smith2025, p. 132]`) or Zotero
item URIs — not as pre-rendered `(Smith, 2025, p. 132)` strings. A draft whose citations are
plain text is a dead end: it cannot go into pandoc or Zotero's word processor plugin without
being redone by hand. Include an appendix listing which sections were drafted with AI
assistance, from the stored `PromptExport` rows.

---

## 8. The loop

```
Zotero: read, highlight, comment
   ↓
Workbench: import → cards
   ↓
Workbench: create notes in Zotero
   ↓
Zotero: drag notes into subcollections            ← the grouping happens HERE
   ↓
Workbench: re-import → groups recovered
   ↓
Workbench: write one proposition per group
   ↓
Workbench: push labels back to Zotero
   ↓
Workbench: structure comparison → cards worth re-reading
   ↓
Workbench: build prompt → Claude Chat → paste draft back → validate
```

Every step is re-runnable and idempotent. The researcher can leave and return at any point.
Re-import never destroys work.

---

## 9. Milestones

Stop at each one and confirm it works before continuing.

**M1 — Zotero adapter.** Connection, capability detection, collection tree, sources,
attachments, annotation index. No UI beyond a status page. Fixture-based tests, no live
Zotero in CI.

**M2 — Import and cards.** Data model, import, locator resolution including EPUB spine
parsing, quote cleaning, Cards screen with filters and search. Read-only. **At this point
the tool is already useful.**

**M3 — Notes into Zotero.** Authorization with the "Always Allow" steering, 50-object
batching, `401` retry, rate-limit pacing, partial-failure reporting, `_KJ/Inbox` creation,
idempotency. Plus a **Revert this batch** action — 400 notes appearing in a library is
frightening without an undo.

**M4 — Placement read-back and Groups.** Recognise `kj-card` notes on re-import, record
placements, count cards not sightings, treat Inbox as unsorted. Groups screen, label
authoring, labels pushed back to Zotero.

**M5 — Add my note.** Comment authoring on quote cards, written back to the Zotero
annotation. This is what §1.4 says is scarce; give it real design attention.

**M6 — Structure.** TF-IDF, Ward, ARI/NMI, contingency view, misfit list.

**M7 — Compose and prompt export.** Question, claims, outline, evidence assignment, the four
export kinds, paste-back validation, Markdown export with citekeys.

**M8 — Direct API (later, not now).** Replace paste-back with a call to the Anthropic API
behind an `LLMProvider` interface. Same prompts, same validators. Keep prompt export working
— it is how the researcher inspects what is being sent.

---

## 10. Out of scope

Zotero plugin packaging. Multi-user or cloud anything. Word export. OCR. AI vision on image
annotations. Automatic literature search. Citation-network analysis. Mobile.

**And specifically:** no card board, no drag-and-drop between groups, no `Cluster` entity, no
vector database. §1.3.

Spatial arrangement of groups and typed relation arrows (causal, mutual, opposing) are a real
future direction and the researcher wants them eventually. Not now. Leave room: nothing in
the data model should block adding coordinates to `Card` or a `round` column to distinguish
successive passes.

---

## 11. Design brief

The subject is a researcher's own reading — marked-up PDFs, marginalia, index cards on a
desk. The audience is one person doing sustained intellectual work in sessions of an hour or
more. The screen's job is **to make a researcher's own fragments legible enough to think
with.**

Design for reading, not for dashboards. The card text is the interface; chrome should
recede. Long sessions mean the palette must be comfortable at hour two, and text must be set
at a real reading measure with a real reading size — not 13px in a data grid.

Make the type do the work of distinguishing **whose words these are**. A quote and the
researcher's own idea about it must be instantly separable at a glance, before reading a
word — by typeface, weight, or ground. This distinction is the product's whole thesis and it
should be the design's most visible commitment.

Avoid the current AI-design defaults: cream background with a serif display face and a
terracotta accent; near-black with one acid accent; broadsheet hairline rules with zero
border-radius. Choose a direction from the subject rather than from that vocabulary, and
state the palette (4–6 named hex values), the display/body/utility faces, and the one
signature element before writing CSS.

Interface copy: name things by what the researcher controls. "Create notes in Zotero", not
"Materialize". "Write a label", not "Submit". An action keeps its name through the whole
flow. Empty states say what to do next — an empty Groups screen explains that grouping
happens in Zotero and how to get there.

Quality floor without announcing it: keyboard focus visible, reduced motion respected,
responsive down to a laptop half-screen (this app will live beside a Zotero window).

---

## 12. Acceptance tests

Each must pass against fixtures; the starred ones also need a manual pass against a real
library.

1. Import of a collection with subcollections yields one card per text annotation, with the
   full chain card → annotation → attachment → source resolvable.
2. Re-import creates zero duplicates and preserves every edit.
3. An annotation carrying both text and a comment produces two linked cards.
4. The same source in two projects imports into both without a constraint violation.
5. An EPUB annotation with no page label produces a `chapter` locator, never a page.
6. A Japanese quotation survives cleaning with its full-width characters intact.
7. \* Creating notes with a single-use key (press "Allow", not "Always Allow") completes
   without hitting the rate limit and without losing cards.
8. \* A partial batch failure reports exactly which cards failed and leaves the rest saved.
9. \* Dragging notes into subcollections in Zotero, then re-importing, recovers the grouping;
   a note in both Inbox and a theme counts once and reports the theme.
10. Writing a label produces one idea card filed into the group's own collection; re-saving
    updates rather than duplicating.
11. Structure comparison returns ARI, NMI, and a misfit list, and warns on a degenerate
    single-cluster result.
12. A section prompt contains only assigned cards, marks idea cards as the researcher's own,
    and flags estimated locators.
13. Paste-back rejects an unknown `[[CITE:]]` ID.
14. Paste-back flags a direct quote altered beyond whitespace and quote-mark normalisation.
15. Paste-back flags a paraphrase whose n-gram overlap with the original is too high.
16. Markdown export emits citekeys, not pre-rendered author-year strings.
17. With writes unavailable (Zotero 9 or refused authorization), import, cards, structure,
    and prompt export all still work.

---

## 13. Working rules for implementation

Build one milestone at a time. For each: inspect what exists, state the plan, implement the
smallest complete vertical slice, add tests, run them, fix, stop at a usable checkpoint.

Where Zotero's behaviour is uncertain, do not guess. Add a diagnostic endpoint, look at the
actual response, and update the typed model. Preserve unknown fields in `raw_json`. The
spike found the `children`-versus-annotations behaviour precisely this way, after an import
silently returned zero cards.

Write tests that would fail if the behaviour regressed — then verify that by breaking the
code on purpose and watching the test fail. Several bugs in the spike survived their first
test because the test asserted something that was true either way.

Original quotation text, source identity, and locator data are never modified by AI output.
Zotero annotations are read-only; the tool writes only standalone notes, collections, and
collection membership. Never delete anything in Zotero that the user did not ask to delete.
