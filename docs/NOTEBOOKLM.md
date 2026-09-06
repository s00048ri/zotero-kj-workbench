# NotebookLM, and how this workbench meets it

**Question:** can this workbench push a Zotero attachment into NotebookLM and
get summaries back? If the desktop side is too hard, would a browser extension
on the web version do?

**Answer, as built:** NotebookLM stays, and the carrying across is the
researcher's. The workbench prepares what goes into a notebook, holds the
address that comes back, writes that address into Zotero where the reading
happens, and keeps whatever the notebook produced. It never talks to Google,
because for a personal account there is nothing to talk to — §1 — and a
research tool cannot depend on an interface with no version number.

Researched 2026-09-05, revised 2026-09-06. Every dated claim below is from
that reading; this is the part of the document most likely to rot.

---

## 0. Decided

**2026-09-06, first pass.** The researcher answered §6: a personal Google
account, a summary per attachment, kept in both places, one manual click
acceptable. Read strictly, those answers pointed away from NotebookLM — a
per-attachment summary is the one use it is not needed for — so `summarise.py`
was built on the Claude path instead. **It has since been deleted**: see the
third pass.

**2026-09-06, second pass.** The researcher said NotebookLM is the point, and
asked for the notebook's link to go into the notes this tool creates, so that
many kinds of summary become available. That is a different and better ask,
and it changes the answer, because it stops needing an API at all:

> The valuable thing about NotebookLM is not that it can summarise a PDF. It
> is the Studio — briefing doc, study guide, FAQ, timeline, mind map, audio
> and video overview, flashcards, quizzes, slide decks — every one of them
> grounded in the same set of sources, asked for as often as you like. None of
> that needs to be automated. It needs to be **reachable**, from where the
> researcher is reading.

So the design is a bridge with a person in the middle of it, and every part of
it is supported:

| Step | Who | How |
|---|---|---|
| Gather the sources | workbench | `notebooklm.bundle()` — DOIs and URLs from Zotero as one bulk-pasteable block; local files with neither gathered into one folder to select all of; **the researcher's own selected passages as one pasted text source** |
| Make the notebook, paste it in | researcher | NotebookLM takes a list of URLs at once, multiple files at once, and pasted text as a source |
| Paste the address back | researcher | one field |
| Write the link into Zotero | workbench | a note on the item, and a link in every card note written afterwards |
| Ask for whatever summary | researcher | NotebookLM's Studio, as many times as they like |
| Keep what came back | workbench | pasted back, stored, and filed in Zotero as NotebookLM's work |

Nothing in that column is scraped, no cookie is borrowed, and nothing breaks
when Google ships a release. What it costs is two pastes per notebook.

**2026-09-06, third pass.** `summarise.py` deleted at the researcher's
instruction, along with its table, its routes, its screen and its tests.
NotebookLM is now the whole of the answer, and nothing in this repository
sends a source to a model.

That removal is worth recording rather than just doing, because it takes the
project back to a line SPEC §0 draws and the first pass had bent: this tool
does not read the literature for the researcher. It carries their selections
somewhere they can be asked about, and carries the answers back. Where the
reading happens is the researcher's business, and NotebookLM is where they
have chosen to do it.

Migration 005 created the summary table and has been deleted; 007 drops the
table with `IF EXISTS`. The gap in the numbering is deliberate — renumbering
006 down into it would be skipped by any database that had already applied
005, which would then never get the notebook tables. `test_store.py` pins
both paths.

**What is deliberately not built:** the unofficial cookie clients of §3C and
the DOM-driving extension of §3D. Both were judged and are still judged
breakable and grey; §5 layer 3 remains where they would go if the two pastes
ever become intolerable.

Everything below is the reasoning that got here.

---

## 1. What NotebookLM is, in September 2026

* It was renamed **Gemini Notebook** in July 2026. The old name still
  dominates search results and the URLs, so this document keeps using it.
* **There is no public consumer API.** No key in a settings page, no
  documented endpoint for a personal account. This has been "coming" for long
  enough that planning around its arrival would be a mistake.
* **There is an enterprise API**, in preview, on
  `ENDPOINT_LOCATION-discoveryengine.googleapis.com/v1alpha`, with
  `notebooks.create` and `notebooks.sources:batchCreate`. It needs a Google
  Cloud project and a Gemini Enterprise licence — not something a single
  researcher signs up for.
* **Workspace Studio** gained NotebookLM steps in 2026: "Add a source to
  Gemini Notebook" and "Ask NotebookLM", triggerable by a Drive folder
  changing. This is the only *supported*, *no-code* automation path, and it
  needs a Workspace account.
* Practical ceilings: 50 sources per notebook on free, 100 on Plus, 300 on
  Pro; 500,000 words or 200 MB per source. A per-source summary of a book is
  comfortably inside them; a whole library is not.

## 2. What is already settled on the Zotero side

Nothing here needs inventing. Both halves of the question have a documented
answer.

**Desktop.** The local API hands over an attachment's bytes:

```
GET /users/0/items/<attachmentKey>/file            302 → file://…
GET /users/0/items/<attachmentKey>/file/view/url   the file:// URL, as text
```

`ZoteroClient.file_url()` in `src/zkj/zotero/client.py` already calls the
second one, for the EPUB locator work. Reading the PDF from that path is a
`Path.read_bytes()` away.

**Web.** For a browser-only design there is no need for Zotero to be running
at all: the Zotero **Web** API is public, documented and stable, and
`GET https://api.zotero.org/users/<id>/items/<key>/file` returns the
attachment to anything holding the user's API key. So "no desktop" is not a
blocker for the Zotero end — only for this repo's end, which is a desktop
program by construction.

**A Zotero plugin proper.** Zotero 7 and 8 take bootstrapped plugins —
`manifest.json` plus `bootstrap.js`, privileged JS, no CORS, full access to
`Zotero.Items`, and a context menu is a few lines. `client.py` was written so
that a plugin front-end stays possible, and it still is. But a plugin buys
only the *trigger* and the *menu item*; it does not make Google's door open.
The hard part is downstream of Zotero, so building a plugin first would be
solving the easy half twice.

## 3. The four doors, and what each one costs

### A. Enterprise API — supported, and out of reach

`sources:batchCreate` takes Drive documents (`documentId` + `mimeType`),
plain text, web URLs, YouTube, Agentspace content. Raw file bytes are not in
that list, so a PDF would go to Drive first and be added by ID. Auth is
ordinary Google Cloud IAM, which means this is the one route where a token
does not expire in a way that strands the user.

Cost: a Gemini Enterprise licence and a Cloud project. Verdict: **the right
answer for an institution, the wrong answer for one researcher.** Worth
keeping the provider interface shaped so this can be dropped in.

### B. Workspace Studio — supported, no code, and indirect

Upload the attachment to a watched Drive folder with the ordinary Drive API;
a Studio flow adds it to a notebook and can run "Ask NotebookLM" against it.

Cost: a Workspace account, and the summary comes back into Google's world
(a Doc, an email), not into ours — so the return leg is a poll of Drive or
Gmail, which is a second integration. Verdict: **a genuinely legitimate path
for Workspace users**, and the cheapest one to prototype, because the
NotebookLM half is configured rather than coded.

### C. Unofficial clients — capable, and built on sand

`notebooklm-py` and its REST wrapper reverse-engineer Google's internal
`batchexecute` RPCs. They log in through a browser, keep the session cookies
(`SID`, `SAPISID`, `__Secure-1PSID`, …) in `~/.notebooklm/storage_state.json`,
and from there they do everything the UI does and a little more: create
notebooks, **upload local files**, ask questions, generate audio, reports,
mind maps, and download the results.

This is the only door that does exactly what the question asks, today, for a
personal account. It is also:

* **breakable without notice** — RPC method IDs are undocumented and change;
* **rate-limited into the dark** — heavy use draws throttling, early session
  expiry, and, per the projects' own warnings, account restrictions;
* **a licensing question we should not answer for the user** — the libraries
  themselves say personal use only, and point at Google's terms.

Verdict: **usable, never load-bearing.** If it goes in, it goes in behind an
explicit opt-in, isolated in one module, with every failure surfaced as
"NotebookLM changed, this feature is off" rather than as a bug in the
workbench.

### D. Browser extension — the user's own hands, automated

This is the shape the question's second half is reaching for, and it is more
workable than it sounds. An extension holds host permissions for
`notebooklm.google.com` and for a local origin, so:

```
extension → GET http://127.0.0.1:8420/api/attachments/<key>/file   (this app)
extension → construct a File, drive NotebookLM's own upload input
extension → read the generated summary out of the page
extension → POST it back to 127.0.0.1:8420
```

Or, with the Zotero Web API in place of the local one, with no desktop at all
and no server of ours.

It runs as the signed-in user, in their session, doing what they could do by
hand — which is a materially different position from C, though not a
different set of *terms*. It breaks on DOM changes rather than on RPC
changes, which is more frequent but far more visible. Several published
extensions already do the URL-source version of this; none of them can reach
a local file, which is exactly the gap this one would fill.

Verdict: **the best fit for the stated question**, and the most maintenance
per feature.

### E. Not NotebookLM at all

`src/zkj/llm.py` already sends prompts to Claude with a key the user supplies
to the running process. Gemini's own API takes a PDF through its Files API.
Either produces a per-attachment summary today, over an interface with a
version number, and the workbench keeps the result instead of scraping it
back out of someone's page.

What is lost is NotebookLM specifically: its grounded citations across a
whole notebook, its audio overviews, and the fact that the user already has
their notebooks there. Those are real, and they are why the question was
asked. But they are not what "summarise this attachment" needs.

## 4. The tension worth naming

SPEC §0 says the value is in the researcher's own selections, and that "a
general AI can summarise a literature it has read; it cannot tell you which
twelve passages you found arresting." A feature that generates a summary of
every attachment points the other way.

That is not a reason to refuse it — a summary of a paper you have not read
yet is how you decide whether to read it, which is upstream of highlighting,
not a substitute for it. But it does set a design rule:

> A machine summary must never be able to become a card, and must never be
> indistinguishable from something the researcher wrote.

Concretely: summaries land as their own kind of note, marked as generated,
outside the `_KJ/Inbox` flow, and the importer skips them the way it would
skip any note it wrote itself.

## 5. Recommendation (as written on 2026-09-05; §0 records what was built)

Three layers, in this order, each useful alone.

**1 — The summary loop, on a stable interface.** `src/zkj/summarise.py`:
take an attachment key, get the bytes through `ZoteroClient`, send them to a
provider, write the result back as a marked note child of the *source* item
via `WriteSession`, recorded in a batch that can be taken back whole like
every other write this app makes. First provider is the existing Claude path,
because it is already wired and already keyed. This is a day or two of work
and it does not depend on anything Google does.

**2 — A NotebookLM hand-off that cannot break.** A provider interface with
one honest implementation: gather the project's attachments, put them where
NotebookLM can see them, and open the notebook. For a Workspace user that is
the Drive folder from door B, and Studio takes it from there. For everyone
else it is a folder and a manifest and one click. No scraping, no cookies,
nothing that stops working when Google ships a release.

**3 — The automated door, opt-in, quarantined.** Whichever of C or D the
user prefers, as `providers/notebooklm_unofficial.py` or a small extension in
`extension/`, behind a setting that is off by default and a screen that says
plainly what it is doing and why it may stop. If it dies, layers 1 and 2 are
untouched.

The Zotero-plugin question resolves itself under this plan: layer 1 needs no
plugin, layer 3's extension is the plugin-shaped piece, and a native Zotero 8
plugin becomes a nice-to-have trigger for a loop that already works — worth
building when there is something to trigger, not before.

## 6. Open questions for the researcher (answered — see §0)

1. Google account type — personal, or Workspace? This picks between doors B
   and D more than anything else does.
2. Is the wanted output a *per-attachment* summary, or one notebook per
   project that can be asked questions across everything in it? The first is
   layer 1; the second is the only thing NotebookLM is actually needed for.
3. Where should a summary live — a Zotero note, a field on the card's source
   in this app, or both?
4. Is a hand-off that ends in one click acceptable, or does it have to be
   untouched-by-hand to be worth building?

---

## Sources

* Zotero local API — https://www.zotero.org/support/dev/web_api/v3/local_api
* Zotero web API — https://www.zotero.org/support/dev/web_api/v3/basics
* Zotero 7 for developers — https://www.zotero.org/support/dev/zotero_7_for_developers
* Zotero 8 for developers — https://www.zotero.org/support/dev/zotero_8_for_developers
* Gemini Notebook Enterprise, notebooks API — https://docs.cloud.google.com/gemini/enterprise/notebooklm-enterprise/docs/api-notebooks
* Gemini Notebook Enterprise, sources API — https://docs.cloud.google.com/gemini/enterprise/notebooklm-enterprise/docs/api-notebooks-sources
* NotebookLM in Workspace Studio — https://workspaceupdates.googleblog.com/2026/05/notebooklm-in-workspace-studio.html
* `notebooklm-py` — https://github.com/teng-lin/notebooklm-py
* `notebooklm-rest-api` — https://github.com/gnh1201/notebooklm-rest-api
* API status overview — https://autocontentapi.com/blog/does-notebooklm-have-an-api
* Source and size limits — https://notebooklm-guide.com/notebooklm-system-limits-benchmarks/
