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
V3 and V4 are unverified here, and V2's behaviour cannot be exercised.

**V1 is resolved** — run on the researcher's own machine, raw output below,
and it changes the spec: the blob-upload path of §4 and §8.2 is not needed.

Started 2026-09-09.

---

## V1. Zotero local API — **RESOLVED**

Run on the researcher's Windows 11 machine against Zotero 10.0.1,
2026-09-09. Long Japanese filenames elided with `...` for width; nothing else
altered.

```
# Phase 0 / V1 - 2026-09-09T13:47:04.1140169+09:00
# Microsoft Windows NT 10.0.26200.0 / PowerShell 5.1.26100.9168

## V1.0 curl's own User-Agent (not Mozilla/, so the header should not be needed)
no header:   200
with header: 200

## V1.0b a browser-like User-Agent, which is what a Chrome extension sends
Mozilla UA, no header:   000
Mozilla UA, with header: 200
## and with an Origin header, which an extension cannot suppress
Origin, with header:     200

## V1.1 find one stored (not linked-url) attachment
attachment key: T3EF2EXE

## V1.2 /file - the question the whole of Track B turns on.
##      Bytes, or a redirect to file://?  (headers only, body suppressed)
HTTP/1.0 302 undefined
X-Zotero-Version: 10.0.1
X-Zotero-Connector-API-Version: 3
Location: file:///C:/Users/nict_/Zotero/storage/T3EF2EXE/%E5%90%89%E6%B0%B8%20-%20...html
Zotero-API-Version: 3
Zotero-Schema-Version: 44
Zotero-Server-ID: FuPHgfjveY71
Content-Length: 0

## V1.2b following the redirect, if there is one
final code: 302  type:   bytes: 0  url: file://C:/Users/nict_/Zotero/storage/T3EF2EXE/%E5%90%89%E6%B0%B8%20-%20...html

## V1.3 /file/view/url - known to work; recorded for completeness
file:///C:/Users/nict_/Zotero/storage/T3EF2EXE/%E5%90%89%E6%B0%B8%20-%20...html

## V1.4 the same two, with a browser User-Agent
file (Mozilla UA): 302
file/view/url (Mozilla UA): 200
```

### V1a. `/file` does **not** return bytes

`HTTP/1.0 302`, `Content-Length: 0`, `Location: file:///C:/Users/...`. curl
declines to follow it — `-L` still ends on 302 with `bytes: 0` — because a
redirect from `http://` to `file://` is not a protocol curl will cross, and
Chrome will not cross it either.

**So a Chrome extension cannot obtain attachment bytes from the local API.**
That is settled, and it is the answer §5 called most important.

### V1b. It does not need to

`/file/view/url` answers `200` with the same path as plain text, browser
User-Agent and all. The bridge runs on the same machine as Zotero, so the
path is all Track B has to send.

**Spec change — §4, §8.2.** `POST /jobs/{job_id}/blob` and the
`blob_pending` kind are **out of v1**. Both tracks send `kind: "local_path"`.
The blob path stays designed but unbuilt, for the case that genuinely needs
it: a Track B talking to a *remote* library over the Web API (§8.3), where
there is no shared filesystem. Building it now would be building for a case
we have evidence does not arise.

### V1c. The browser guard behaves exactly as documented

| Request | Result |
|---|---|
| curl's own UA, no header | `200` |
| `Mozilla/…` UA, no header | **`000`** — no response at all |
| `Mozilla/…` UA + `zotero-allowed-request: 1` | `200` |
| …plus `Origin: chrome-extension://…` | `200` |

`000` is curl for "the connection gave me nothing", which matches the
documented behaviour precisely: browser-shaped requests are **dropped**, not
refused. There is no status code to check for and no error body to read, so a
client that omits the header sees a timeout or a connection error and will
tend to misdiagnose it as "Zotero is not running".

**The header is mandatory for Track B, not advisory.** An extension service
worker always sends `Origin` and cannot suppress it.

The good news is the last row: an `Origin` header does **not** disqualify the
request once `zotero-allowed-request` is present.

### V1d. Still open — one narrow question

The response carries no `Access-Control-Allow-Origin` (see the full header
dump above: only `X-Zotero-*`, `Location`, `Zotero-*`, `Content-Length`). So
a **page-context** fetch will be blocked by the browser no matter what Zotero
returns.

An extension service worker holding `host_permissions` for
`http://localhost:23119/*` is exempt from CORS, so it should be unaffected —
**FROM DOCUMENTATION, UNVERIFIED.** curl cannot answer this because curl does
not enforce CORS; only a real extension can. It is the one remaining V1
question and belongs with M5 rather than blocking anything before it.

### V1e. Two details worth carrying into the code

* The status line is `HTTP/1.0 302 undefined` — Zotero sends the literal word
  `undefined` as the reason phrase. Harmless to well-behaved clients, but
  anything that parses the reason phrase will be surprised.
* The `file://` URL is percent-encoded UTF-8 with a Windows drive letter.
  `zkj.zotero.client.file_path()` in this repository already decodes exactly
  this and is pinned by tests; the bridge should reuse that logic rather than
  write a second one.

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

1. ~~`verify.ps1` on the machine running Zotero~~ — **done**, V1 above.
2. A throwaway notebook and one `notebooklm login` → settles V2's behaviour.
   This is the only thing M1 is waiting on to be *proven*; it is not waiting
   on it to be *written*.
3. V3 and V4 come with their own milestones and can wait. V1d joins V4 at M5.

M1 (`nbbridge send`) is unblocked.
