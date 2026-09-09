# Phase 0 — findings

Against [docs/spec-nblm.md](spec-nblm.md) §5. The spec asks that verified fact
and assumption be kept apart, so every claim here carries one of:

* **VERIFIED HERE** — run in this repository's development container, output
  quoted.
* **VERIFIED ON THE RESEARCHER'S MACHINE** — observed in a real session
  against their Zotero, quoted.
* **FROM DOCUMENTATION** — read, not run. Believed, not proven.
* **UNVERIFIED** — needs a machine this work has no access to. Named, with the
  command that would settle it.

The development container has no Zotero, no Chrome, and no Google session, so
V1's decisive step, all of V3, and all of V4 are unverified here. Everything
that *can* be settled without them has been.

Started 2026-09-09.

---

## V1. Zotero local API — can a file be fetched?

### V1a. `/file/view/url` returns a `file://` URL — **VERIFIED ON THE RESEARCHER'S MACHINE**

Their Zotero 10.0.1, through this repository's `doctor`, sampled twelve stored
attachments and resolved every one:

```
PASS  Attachment files resolve
      All 12 sampled attachments resolved.
      · resolved, for example: C:\Users\nict_\Zotero\storage\T3EF2EXE\吉永 - 2025 - Ai ガバナンスの相互運用性：原則から実践へ ─ Oecd-Gpai の取り組みを通じた考察.html
```

`GET /users/0/items/<key>/file/view/url` answers with the attachment's
`file://` URL as plain text. Percent-encoded non-ASCII, spaces and a Windows
drive letter all survive the round trip.

### V1b. `/file` — **UNVERIFIED**, and it is the most important line in this document

**FROM DOCUMENTATION**, `/items/<key>/file` answers with a **302 to a `file://`
URL** rather than the bytes. If that holds, it decides Track B's design, and
not in the direction §8.2 assumed:

* a browser cannot follow a redirect to `file://` — Chrome refuses it — so a
  Chrome extension **cannot** obtain attachment bytes from the local API;
* but it does not need to. `/file/view/url` hands over the **path**, and the
  bridge is on the same machine, so Track B can send `kind: "local_path"` and
  never move a byte over HTTP.

If that is confirmed, **`POST /jobs/{job_id}/blob` is not needed for v1** and
the `blob_pending` branch of ItemPayload can wait for a case that actually
needs it — a Track B running against a *remote* Zotero, which is the Web API
fallback of §8.3, not the local one.

Settle it with `bash docs/phase0/verify.sh` on the machine running Zotero; it
records raw headers for exactly this.

### V1c. Browser-shaped requests — **FROM DOCUMENTATION**

Zotero 10 and later drop requests that look like they come from a browser —
a `User-Agent` starting with `Mozilla/`, **or any `Origin` header at all** —
unless they carry `Zotero-Allowed-Request`. The local API also does not answer
cross-origin requests: web pages are not meant to reach it.

A Chrome extension's service worker sends `Origin: chrome-extension://<id>`
and cannot suppress it, so the header is mandatory rather than a nicety. CORS
itself should not bite, because an extension holding `host_permissions` for
`http://localhost:23119/*` is exempt — **UNVERIFIED**, and the second half of
V1 the spec asks for. `verify.sh` records the curl equivalent (Origin header
present, allowed-request header present); the extension case still has to be
run from a real service worker.

---

## V2. `notebooklm-py` — **VERIFIED HERE** (surface only; no Google session)

Version **0.8.2**, installed and read. Nothing was called against Google.

```
$ uv pip install --target /tmp/nlmpkg notebooklm-py
 + notebooklm-py==0.8.2
```

### What the spec needed to know

| Question | Answer |
|---|---|
| Can a **local file** be added as a source? | Yes — `sources.add_file()` |
| Can a source be **renamed**? (§12.5) | Yes — `sources.rename()`, **and** `add_file` takes `title=` |
| Can **text** be added as a source? (§9.2) | Yes — `sources.add_text(notebook_id, title, content)` |
| Notebook list / create? | `notebooks.list()`, `notebooks.create(title)` |
| Is there an auth-expiry exception? (§9.3) | Yes — `AuthError`, plus `RateLimitError`, `NotebookLimitError` |
| Where does the session live? | `~/.notebooklm`, overridable with `NOTEBOOKLM_HOME`; profiles underneath |

```python
async def add_file(
    self, notebook_id: str, file_path: str | Path, mime_type: str | None = None,
    *, wait: bool = False, wait_timeout: float = 120.0,
    title: str | None = None,
    on_progress: Callable[[int, int], object] | None = None,
) -> Source: ...

async def add_text(
    self, notebook_id: str, title: str, content: str,
    *, wait: bool = False, wait_timeout: float = 120.0, idempotent: bool = False,
) -> Source: ...

async def rename(
    self, notebook_id: str, source_id: str, new_title: str,
    *, return_object: bool = True,
) -> Source | None: ...
```

### What this changes in the spec

**§12.5 is resolved, and §9.2 gets simpler.** `add_file` accepts `title=`
directly, so the bibliographic name is set *as the source is created*. There
is no need to upload and then rename, and therefore no window in which the
notebook shows `paper.pdf`.

The API is **async throughout**, and `wait=`/`on_progress=` are built in — so
§9.3's sequential worker and the progress reporting in `/jobs/{id}` have
support underneath them rather than needing to be invented.

### Still **UNVERIFIED** — needs a Google session

Everything about behaviour: the size ceiling, the interval that stays under
rate limiting, what a real expiry looks like on the wire, and how long a large
upload takes. §5's V2 list stands as written. These need a throwaway notebook
and cannot be answered by reading.

**Pin the version.** `notebooklm-py==0.8.2`, exactly, per §11.

---

## V3. Zotero plugin — **UNVERIFIED**

No Zotero here. `getSelectedItems()`, `getAttachments()`/`getFilePath()`, the
imported-vs-linked distinction and submenu construction all need the real
client, per §5 V3.

One thing is already known from this repository's own work and is worth
carrying over: **an attachment's file path is reliably obtainable**, and
non-ASCII paths survive (V1a). Track A's `collect.ts` is therefore expected to
be the straightforward half.

## V4. zotero.org DOM — **UNVERIFIED**

Needs a logged-in browser. Per §8.3 this is the branch that decides whether
Track B can use `chrome.contextMenus` at all or must inject its own selection
UI. Nothing here can narrow it.

## §8.4 direct mode — **NOT INVESTIGATED**

Out of v1 scope by the spec. Needs a logged-in browser, like V4.

---

## What is needed before M1

1. `bash docs/phase0/verify.sh` on the machine running Zotero → settles V1b
   and V1c's curl half.
2. A throwaway notebook and one `notebooklm login` → settles V2's behaviour.
3. V3 and V4 come with their own milestones and can wait.

M1 (`nbbridge send`) can begin on V1b and V2 alone. It does not depend on V3
or V4.
