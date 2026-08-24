#!/usr/bin/env python3
"""
zkj_v0 -- Zotero KJ Research Assistant, validation spike (v0)

PURPOSE
    Answer one question before any real application is built:
    are your Zotero highlights and notes usable as research cards at all?

    This script does NOT write to Zotero. It does NOT call an LLM.
    It reads your local Zotero library, materialises cards into SQLite,
    and produces exports you can read with your own eyes.

WHAT IT MODELS
    quote cards  -- the source's words (annotation text). Immutable evidence.
    idea  cards  -- your words (annotation comments, child notes, standalone
                    notes). These are first-class, not decoration on a quote.
    A comment written on a highlight becomes its own idea card, linked to the
    quote card it came from. That link is the raw material of an argument:
    "this is what the source said" + "this is what I think it means".

REQUIREMENTS
    Zotero desktop running, with
        Settings -> Advanced -> "Allow other applications on this computer
        to communicate with Zotero"
    enabled. Read access needs no key and no Zotero 10.

    Python 3.10+. Core commands are stdlib-only.
    `compare` additionally needs: pip install scikit-learn

USAGE
    python zkj_v0.py check
    python zkj_v0.py collections
    python zkj_v0.py import --collection ABCD1234 --project "agentic-governance"
    python zkj_v0.py cards --project "agentic-governance" --export cards.md
    python zkj_v0.py compare --project "agentic-governance"
    python zkj_v0.py selftest
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional
from xml.etree import ElementTree as ET

# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------

ZOTERO_BASE = os.environ.get("ZOTERO_LOCAL_API", "http://localhost:23119/api")
ZOTERO_USER = os.environ.get("ZOTERO_USER_ID", "0")
DB_PATH = os.environ.get("ZKJ_DB", "zkj_v0.sqlite3")
USER_AGENT = "zkj-v0/0.1 (local research spike)"
GOOGLE_BOOKS_ENDPOINT = "https://www.googleapis.com/books/v1/volumes"

# Annotation types Zotero can produce. `ink` and `image` carry no text.
TEXTUAL_ANNOTATION_TYPES = {"highlight", "underline", "text", "note"}
NON_TEXTUAL_ANNOTATION_TYPES = {"image", "ink"}


# --------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def sha256(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(b"\x1f")
        if p is None:
            continue
        if not isinstance(p, str):
            p = json.dumps(p, sort_keys=True, ensure_ascii=False)
        h.update(p.encode("utf-8"))
    return h.hexdigest()


_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(r"</(p|div|li|h[1-6]|blockquote|tr)\s*>", re.I)
_BR_RE = re.compile(r"<br\s*/?>", re.I)


def html_to_text(s: Optional[str]) -> str:
    """Zotero notes are HTML. Flatten to text without adding dependencies."""
    if not s:
        return ""
    s = _BR_RE.sub("\n", s)
    s = _BLOCK_RE.sub("\n", s)
    s = _TAG_RE.sub("", s)
    s = html.unescape(s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def normalise_quote(s: Optional[str]) -> str:
    """
    Conservative cleanup of PDF-extracted text.

    Deliberately NOT NFKC: NFKC rewrites full-width characters and would
    silently alter Japanese quotations. We only expand ligatures, drop soft
    hyphens, repair end-of-line hyphenation in Latin script, and collapse
    whitespace. The raw string is always kept alongside this one.
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s)
    for lig, repl in (("\ufb00", "ff"), ("\ufb01", "fi"), ("\ufb02", "fl"),
                      ("\ufb03", "ffi"), ("\ufb04", "ffl"), ("\ufb05", "st")):
        s = s.replace(lig, repl)
    s = s.replace("\u00ad", "")          # soft hyphen
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s)   # hyphenated line break
    s = re.sub(r"\s*\n\s*", " ", s)
    s = re.sub(r"[ \t\u00a0]{2,}", " ", s)
    return s.strip()


def first_year(item: dict) -> Optional[str]:
    date = (item.get("date") or "").strip()
    m = re.search(r"(1[5-9]\d{2}|20\d{2}|21\d{2})", date)
    return m.group(1) if m else None


def creators_short(item: dict) -> str:
    creators = item.get("creators") or []
    names = []
    for c in creators:
        if c.get("creatorType") not in (None, "author", "editor"):
            continue
        names.append(c.get("lastName") or c.get("name") or "")
    names = [n for n in names if n]
    if not names:
        return "Anon."
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} & {names[1]}"
    return f"{names[0]} et al."


# --------------------------------------------------------------------------
# Zotero local API client (read-only)
# --------------------------------------------------------------------------

class ZoteroError(RuntimeError):
    pass


class ZoteroWriteError(ZoteroError):
    def __init__(self, status: int, headers: dict, message: str):
        super().__init__(message)
        self.status = status
        self.headers = headers


@dataclass
class ServerInfo:
    api_version: Optional[str]
    server_id: Optional[str]
    schema_version: Optional[str]


class ZoteroClient:
    """
    Read-only client for the Zotero local API.

    Notes on the local API that shape this code:
      * reads need no authentication and no API key;
      * results are NOT paginated by default -- a whole collection comes back
        in one response, so no cursor logic is needed;
      * every response carries Zotero-Server-ID (Zotero 10+). We cache it per
        project so a later version of this tool can refuse to reuse stored
        object versions against a different database.
    """

    def __init__(self, base: str = ZOTERO_BASE, user: str = ZOTERO_USER):
        self.base = base.rstrip("/")
        self.prefix = f"{self.base}/users/{user}"
        self._server: Optional[ServerInfo] = None

    def _request(self, url: str, expect_json: bool = True, method: str = "GET",
                 body: Any = None, extra_headers: Optional[dict] = None
                 ) -> tuple[Any, dict]:
        headers = {
            "Zotero-API-Version": "3",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                resp_headers = dict(resp.headers.items())
        except urllib.error.HTTPError as e:
            if e.code == 403 and method == "GET":
                raise ZoteroError(
                    "403 Forbidden. Enable Zotero Settings -> Advanced -> "
                    "'Allow other applications on this computer to "
                    "communicate with Zotero'."
                ) from e
            detail = ""
            try:
                detail = e.read().decode("utf-8", "ignore")[:300]
            except Exception:
                pass
            raise ZoteroWriteError(e.code, dict(e.headers.items()),
                                   f"HTTP {e.code} for {method} {url}: "
                                   f"{e.reason} {detail}") from e
        except urllib.error.URLError as e:
            raise ZoteroError(
                f"Cannot reach Zotero at {self.base}. Is Zotero running? ({e.reason})"
            ) from e
        if not expect_json:
            return raw, resp_headers
        if not raw:
            return None, resp_headers
        return json.loads(raw.decode("utf-8")), resp_headers

    def server_info(self) -> ServerInfo:
        if self._server is None:
            _, headers = self._request(self.base + "/", expect_json=False)
            self._server = ServerInfo(
                api_version=headers.get("Zotero-API-Version"),
                server_id=headers.get("Zotero-Server-ID"),
                schema_version=headers.get("Zotero-Schema-Version"),
            )
        return self._server

    def collections(self) -> list[dict]:
        data, _ = self._request(f"{self.prefix}/collections?limit=0")
        return data or []

    def collection_items_top(self, key: str) -> list[dict]:
        data, _ = self._request(f"{self.prefix}/collections/{key}/items/top?limit=0")
        return data or []

    def children(self, key: str) -> list[dict]:
        data, _ = self._request(f"{self.prefix}/items/{key}/children?limit=0")
        return data or []

    def file_url(self, attachment_key: str) -> Optional[str]:
        """Local-API-only endpoint: returns a file:// URL as plain text."""
        try:
            raw, _ = self._request(
                f"{self.prefix}/items/{attachment_key}/file/view/url",
                expect_json=False,
            )
            return raw.decode("utf-8").strip() if raw else None
        except ZoteroError:
            return None

    def items(self, **params: Any) -> list[dict]:
        """Library-wide item query, used by `diagnose`."""
        params.setdefault("limit", 0)
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        data, _ = self._request(f"{self.prefix}/items?{qs}")
        return data or []

    # -- writes (Zotero 10+) ------------------------------------------------

    def authorize_write(self, app_name: str = "zkj v0") -> dict:
        """
        Ask Zotero for a local write key. Shows a dialog in Zotero.

        Returns {"key": ..., "remember": bool}. `remember` is True only if the
        user chose "Always Allow"; otherwise the key is consumed by the first
        successful write and the next one needs a fresh dialog. Zotero accepts
        at most five dialog-showing requests per minute.
        """
        info = self.server_info()
        if not info.server_id:
            raise ZoteroError(
                "This Zotero has no Zotero-Server-ID header, so it predates "
                "local API write support (Zotero 10+). Upgrade Zotero to "
                "materialise cards as notes."
            )
        try:
            data, _ = self._request(
                f"{self.base}/local/authorize", method="POST",
                body={"appName": app_name},
                extra_headers={"Zotero-Server-ID": info.server_id},
            )
        except ZoteroWriteError as e:
            if e.status == 403:
                raise ZoteroError("You denied the write request in Zotero.") from e
            if e.status == 429:
                wait = e.headers.get("Retry-After", "60")
                raise ZoteroError(
                    f"Zotero is rate-limiting authorization prompts. "
                    f"Wait {wait}s and try again -- and choose 'Always Allow' "
                    f"this time so a single key covers the whole run."
                ) from e
            raise
        return data or {}

    def _write(self, path: str, payload: Any, api_key: str,
               method: str = "POST") -> dict:
        info = self.server_info()
        data, _ = self._request(
            f"{self.prefix}{path}", method=method, body=payload,
            extra_headers={
                "Zotero-Server-ID": info.server_id or "",
                "Zotero-API-Key": api_key,
            },
        )
        return data or {}

    def create_items(self, items: list[dict], api_key: str) -> dict:
        return self._write("/items", items, api_key)

    def create_collections(self, collections: list[dict], api_key: str) -> dict:
        return self._write("/collections", collections, api_key)


WRITE_BATCH = 50   # Web API caps a multi-object write at 50 objects


def parse_write_result(result: dict, n_sent: int) -> tuple[dict[int, str], dict[int, str]]:
    """Split a multi-object write response into {index: key} and {index: error}."""
    ok: dict[int, str] = {}
    for idx, key in (result.get("success") or {}).items():
        ok[int(idx)] = key
    for idx, obj in (result.get("successful") or {}).items():
        d = obj.get("data", obj)
        if d.get("key"):
            ok[int(idx)] = d["key"]
    errors: dict[int, str] = {}
    for idx, err in (result.get("failed") or {}).items():
        errors[int(idx)] = f"{err.get('code')}: {err.get('message')}"
    for i in range(n_sent):
        if i not in ok and i not in errors:
            errors[i] = "no result reported"
    return ok, errors


# --------------------------------------------------------------------------
# collection tree
# --------------------------------------------------------------------------

@dataclass
class CollNode:
    key: str
    name: str
    parent: Optional[str]
    children: list["CollNode"] = field(default_factory=list)
    path: str = ""
    depth: int = 0


def build_tree(raw_collections: list[dict]) -> dict[str, CollNode]:
    nodes: dict[str, CollNode] = {}
    for c in raw_collections:
        d = c.get("data", c)
        parent = d.get("parentCollection")
        nodes[d["key"]] = CollNode(
            key=d["key"],
            name=d.get("name", "(untitled)"),
            parent=parent if parent else None,
        )
    for node in nodes.values():
        if node.parent and node.parent in nodes:
            nodes[node.parent].children.append(node)

    def assign(node: CollNode, prefix: str, depth: int) -> None:
        node.path = f"{prefix}/{node.name}" if prefix else node.name
        node.depth = depth
        for ch in sorted(node.children, key=lambda n: n.name):
            assign(ch, node.path, depth + 1)

    for node in nodes.values():
        if not node.parent or node.parent not in nodes:
            assign(node, "", 0)
    return nodes


def subtree_keys(nodes: dict[str, CollNode], root_key: str) -> list[str]:
    if root_key not in nodes:
        raise ZoteroError(f"Collection {root_key} not found in this library.")
    out, stack = [], [nodes[root_key]]
    while stack:
        n = stack.pop()
        out.append(n.key)
        stack.extend(n.children)
    return out


# --------------------------------------------------------------------------
# locator resolution
# --------------------------------------------------------------------------

@dataclass
class Locator:
    type: str            # page | chapter | cfi | position | none
    value: str
    source: str          # page_label | page_index | epub_page_list | epub_spine | cfi | none
    estimated: bool = False
    detail: dict = field(default_factory=dict)

    def render(self) -> str:
        if self.type == "none":
            return ""
        if self.type == "page":
            return f"p. {self.value}" + (" (est.)" if self.estimated else "")
        if self.type == "chapter":
            return f"ch. {self.value}"
        if self.type == "cfi":
            return f"loc. {self.value[:40]}"
        return self.value


_SPINE_STEP_RE = re.compile(r"/(\d+)")


def cfi_spine_index(cfi: str) -> Optional[int]:
    """
    Extract the spine itemref index from an EPUB CFI.

    epubcfi(/6/14[chap05ref]!/4/10/2/1:3)
             ^^^^^ package path; last even step is the itemref.
    CFI element steps are even and 1-based-doubled, so index = step/2 - 1.
    """
    if not cfi:
        return None
    m = re.search(r"epubcfi\((.*)\)", cfi)
    body = m.group(1) if m else cfi
    head = body.split("!")[0]
    steps = _SPINE_STEP_RE.findall(head)
    if not steps:
        return None
    try:
        last = int(steps[-1])
    except ValueError:
        return None
    if last % 2 != 0 or last < 2:
        return None
    return last // 2 - 1


class EpubIndex:
    """Spine layout of one EPUB, used to turn a CFI into a chapter + fraction."""

    def __init__(self, path: str):
        self.path = path
        self.spine: list[tuple[str, str]] = []   # (idref, href)
        self.titles: dict[int, str] = {}
        self.char_counts: list[int] = []
        self.total_chars = 0
        self.page_list: bool = False
        self._load()

    def _load(self) -> None:
        with zipfile.ZipFile(self.path) as z:
            container = z.read("META-INF/container.xml")
            root = ET.fromstring(container)
            ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
            rootfile = root.find(".//c:rootfile", ns)
            if rootfile is None:
                raise ValueError("no rootfile in container.xml")
            opf_path = rootfile.attrib["full-path"]
            opf = ET.fromstring(z.read(opf_path))
            opf_dir = os.path.dirname(opf_path)

            opf_ns = {"o": "http://www.idpf.org/2007/opf"}
            manifest: dict[str, tuple[str, str]] = {}
            for it in opf.findall(".//o:manifest/o:item", opf_ns):
                manifest[it.attrib["id"]] = (
                    it.attrib.get("href", ""),
                    it.attrib.get("properties", ""),
                )
            for idref_el in opf.findall(".//o:spine/o:itemref", opf_ns):
                idref = idref_el.attrib.get("idref", "")
                href = manifest.get(idref, ("", ""))[0]
                full = os.path.normpath(os.path.join(opf_dir, href)).replace("\\", "/")
                self.spine.append((idref, full))

            self.page_list = any(
                "page-list" in (z.read(os.path.normpath(
                    os.path.join(opf_dir, href)).replace("\\", "/")
                ).decode("utf-8", "ignore")
                    if "nav" in props else "")
                for href, props in manifest.values()
            ) if manifest else False

            for idx, (_idref, href) in enumerate(self.spine):
                try:
                    doc = z.read(href).decode("utf-8", "ignore")
                except KeyError:
                    self.char_counts.append(0)
                    continue
                title = ""
                tm = re.search(r"<title[^>]*>(.*?)</title>", doc, re.S | re.I)
                if tm:
                    title = html_to_text(tm.group(1))
                if not title:
                    hm = re.search(r"<h[1-3][^>]*>(.*?)</h[1-3]>", doc, re.S | re.I)
                    if hm:
                        title = html_to_text(hm.group(1))[:80]
                if title:
                    self.titles[idx] = title
                self.char_counts.append(len(html_to_text(doc)))
        self.total_chars = sum(self.char_counts) or 1

    def locate(self, spine_idx: int) -> tuple[str, float]:
        """Return (chapter label, fraction through the book at chapter start)."""
        spine_idx = max(0, min(spine_idx, len(self.spine) - 1))
        before = sum(self.char_counts[:spine_idx])
        fraction = before / self.total_chars
        label = self.titles.get(spine_idx) or self.spine[spine_idx][0] or f"section {spine_idx + 1}"
        return label, fraction


def resolve_locator(
    ann: dict,
    attachment: dict,
    epub_index: Optional[EpubIndex],
    page_count: Optional[int],
) -> Locator:
    """
    Never invent a page number silently.

    PDF   : pageLabel -> position.pageIndex+1
    EPUB  : pageLabel (real, from the book's page-list) -> chapter (+ optional
            estimated print page, flagged) -> raw CFI
    other : none
    """
    page_label = (ann.get("annotationPageLabel") or "").strip()
    position = ann.get("annotationPosition")
    if isinstance(position, str):
        try:
            position = json.loads(position)
        except json.JSONDecodeError:
            position = {}
    position = position or {}
    content_type = (attachment.get("contentType") or "").lower()
    is_epub = "epub" in content_type

    if not is_epub:
        if page_label:
            return Locator("page", page_label, "page_label")
        if "pageIndex" in position:
            try:
                return Locator("page", str(int(position["pageIndex"]) + 1), "page_index")
            except (TypeError, ValueError):
                pass
        return Locator("none", "", "none")

    # EPUB
    if page_label:
        return Locator("page", page_label, "epub_page_list")

    cfi = position.get("value") or ""
    spine_idx = cfi_spine_index(cfi)
    if epub_index is not None and spine_idx is not None:
        label, fraction = epub_index.locate(spine_idx)
        detail = {"cfi": cfi, "fraction": round(fraction, 4), "spine_index": spine_idx}
        if page_count:
            est = max(1, min(page_count, round(fraction * page_count) or 1))
            detail["estimated_page"] = est
            detail["page_count_source"] = "google_books"
            return Locator("chapter", label, "epub_spine", estimated=True, detail=detail)
        return Locator("chapter", label, "epub_spine", estimated=True, detail=detail)
    if cfi:
        return Locator("cfi", cfi, "cfi", estimated=True, detail={"cfi": cfi})
    return Locator("none", "", "none")


# --------------------------------------------------------------------------
# Google Books (optional, opt-in, cached)
# --------------------------------------------------------------------------

def google_books_page_count(
    conn: sqlite3.Connection,
    isbn: Optional[str],
    title: Optional[str],
    author: Optional[str],
) -> Optional[int]:
    """
    Best-effort print page count, used ONLY to annotate an EPUB chapter
    locator with an estimate. Google Books page counts are frequently the
    ebook's own pagination, not a print edition's, so anything derived from
    this is flagged `estimated` and must never be cited without checking.
    """
    key = (isbn or "") + "|" + (title or "")[:120] + "|" + (author or "")[:60]
    if not key.strip("|"):
        return None
    row = conn.execute(
        "SELECT page_count FROM gbooks_cache WHERE cache_key = ?", (key,)
    ).fetchone()
    if row is not None:
        return row["page_count"]

    if isbn:
        q = f"isbn:{re.sub(r'[^0-9Xx]', '', isbn)}"
    else:
        q = " ".join(filter(None, [f'intitle:"{title}"' if title else None,
                                   f'inauthor:"{author}"' if author else None]))
    url = f"{GOOGLE_BOOKS_ENDPOINT}?q={urllib.parse.quote(q)}&maxResults=5"
    page_count = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for item in data.get("items", []):
            pc = item.get("volumeInfo", {}).get("pageCount")
            if isinstance(pc, int) and pc > 0:
                page_count = pc
                break
    except Exception as e:                       # network optional by design
        log(f"  google books lookup failed ({e}); continuing without estimate")
    conn.execute(
        "INSERT OR REPLACE INTO gbooks_cache (cache_key, page_count, fetched_at) "
        "VALUES (?, ?, ?)", (key, page_count, now_iso())
    )
    time.sleep(0.2)
    return page_count


# --------------------------------------------------------------------------
# database
# --------------------------------------------------------------------------

CARD_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    human_id TEXT NOT NULL,
    origin_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('quote','idea','image')),
    origin TEXT NOT NULL CHECK (origin IN
        ('annotation_text','annotation_comment','child_note','standalone_note',
         'group_label','manual')),
    text TEXT NOT NULL,
    text_raw TEXT,
    human_label TEXT,
    source_id INTEGER REFERENCES source(id) ON DELETE CASCADE,
    annotation_id INTEGER REFERENCES annotation(id) ON DELETE CASCADE,
    zotero_note_key TEXT,       -- the standalone note THIS TOOL created
    origin_note_key TEXT,       -- the Zotero note the card's text came from
    parent_card_id INTEGER REFERENCES card(id) ON DELETE SET NULL,
    prior_collection_id INTEGER REFERENCES collection(id) ON DELETE SET NULL,
    prior_path TEXT,
    prior_ambiguous INTEGER NOT NULL DEFAULT 0,
    locator_type TEXT,
    locator_value TEXT,
    locator_source TEXT,
    locator_estimated INTEGER NOT NULL DEFAULT 0,
    locator_detail_json TEXT,
    color TEXT,
    status TEXT NOT NULL DEFAULT 'inbox',
    kj_collection_keys_json TEXT,
    kj_path TEXT,
    materialized_at TEXT,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (project_id, origin_key)
);
"""

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS project (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    zotero_server_id TEXT,
    root_collection_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_import_at TEXT
);

CREATE TABLE IF NOT EXISTS collection (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    zotero_collection_key TEXT NOT NULL,
    parent_key TEXT,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    depth INTEGER NOT NULL,
    UNIQUE (project_id, zotero_collection_key)
);

CREATE TABLE IF NOT EXISTS source (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    zotero_item_key TEXT NOT NULL,
    item_type TEXT,
    title TEXT,
    creators_json TEXT,
    creators_short TEXT,
    year TEXT,
    publication_title TEXT,
    doi TEXT,
    isbn TEXT,
    url TEXT,
    raw_json TEXT,
    UNIQUE (project_id, zotero_item_key)          -- per-project, not global
);

CREATE TABLE IF NOT EXISTS source_collection (
    source_id INTEGER NOT NULL REFERENCES source(id) ON DELETE CASCADE,
    collection_id INTEGER NOT NULL REFERENCES collection(id) ON DELETE CASCADE,
    PRIMARY KEY (source_id, collection_id)
);

CREATE TABLE IF NOT EXISTS attachment (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES source(id) ON DELETE CASCADE,
    zotero_attachment_key TEXT NOT NULL,
    content_type TEXT,
    title TEXT,
    filename TEXT,
    link_mode TEXT,
    raw_json TEXT,
    UNIQUE (source_id, zotero_attachment_key)     -- was missing entirely
);

CREATE TABLE IF NOT EXISTS annotation (
    id INTEGER PRIMARY KEY,
    attachment_id INTEGER NOT NULL REFERENCES attachment(id) ON DELETE CASCADE,
    zotero_annotation_key TEXT NOT NULL,
    annotation_type TEXT,
    text_raw TEXT,
    comment_raw TEXT,
    color TEXT,
    page_label TEXT,
    sort_index TEXT,
    position_json TEXT,
    date_modified TEXT,
    raw_json TEXT,
    content_hash TEXT NOT NULL,
    UNIQUE (attachment_id, zotero_annotation_key) -- was UNIQUE(key) globally
);

-- card table is created separately from CARD_DDL

CREATE INDEX IF NOT EXISTS idx_card_project_kind ON card(project_id, kind);
CREATE INDEX IF NOT EXISTS idx_card_prior ON card(project_id, prior_collection_id);

CREATE TABLE IF NOT EXISTS gbooks_cache (
    cache_key TEXT PRIMARY KEY,
    page_count INTEGER,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS write_auth (
    server_id TEXT PRIMARY KEY,
    api_key TEXT NOT NULL,
    remember INTEGER NOT NULL DEFAULT 0,
    granted_at TEXT NOT NULL
);
"""

# columns added after the first release; applied to existing databases
MIGRATIONS: dict[str, dict[str, str]] = {
    "card": {
        "origin_note_key": "TEXT",
        "kj_collection_keys_json": "TEXT",
        "kj_path": "TEXT",
        "materialized_at": "TEXT",
    },
    "project": {
        "kj_root_key": "TEXT",
        "kj_inbox_key": "TEXT",
    },
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, cols in MIGRATIONS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def open_db(path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(CARD_DDL.format(table="card"))
    conn.executescript(SCHEMA)
    _migrate(conn)
    _rebuild_card_if_stale(conn)
    conn.commit()
    return conn


def _rebuild_card_if_stale(conn: sqlite3.Connection) -> None:
    """
    SQLite cannot alter a CHECK constraint, so a database created before
    'group_label' existed has to be rebuilt. Copy the columns both versions
    share and leave the rest at their defaults.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='card'"
    ).fetchone()
    if not row or "group_label" in (row["sql"] or ""):
        return
    log("migrating card table (new origin values)")
    old_cols = [r["name"] for r in conn.execute("PRAGMA table_info(card)")]
    conn.executescript(CARD_DDL.format(table="card_new"))
    new_cols = [r["name"] for r in conn.execute("PRAGMA table_info(card_new)")]
    shared = [c for c in old_cols if c in new_cols]
    cols = ", ".join(shared)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(f"INSERT INTO card_new ({cols}) SELECT {cols} FROM card")
    conn.execute("DROP TABLE card")
    conn.execute("ALTER TABLE card_new RENAME TO card")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_card_project_kind "
                 "ON card(project_id, kind)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_card_prior "
                 "ON card(project_id, prior_collection_id)")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()


def upsert(conn: sqlite3.Connection, table: str, keys: dict, values: dict) -> int:
    where = " AND ".join(f"{k} = ?" for k in keys)
    row = conn.execute(
        f"SELECT id FROM {table} WHERE {where}", tuple(keys.values())
    ).fetchone()
    if row:
        if values:
            sets = ", ".join(f"{k} = ?" for k in values)
            conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?",
                         tuple(values.values()) + (row["id"],))
        return row["id"]
    payload = {**keys, **values}
    cols = ", ".join(payload)
    marks = ", ".join("?" for _ in payload)
    cur = conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})",
                       tuple(payload.values()))
    return cur.lastrowid


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------

@dataclass
class ImportStats:
    sources: int = 0
    attachments: int = 0
    annotations: int = 0
    quote_cards: int = 0
    idea_cards: int = 0
    image_cards: int = 0
    skipped_empty: int = 0
    changed: int = 0
    epub_attachments: int = 0
    locator_none: int = 0
    locator_estimated: int = 0
    placements_read: int = 0
    still_in_inbox: int = 0

    def render(self) -> str:
        return "\n".join([
            f"  sources          {self.sources}",
            f"  attachments      {self.attachments} (epub: {self.epub_attachments})",
            f"  annotations      {self.annotations}",
            f"  new quote cards  {self.quote_cards}",
            f"  new idea cards   {self.idea_cards}",
            f"  new image cards  {self.image_cards}",
            f"  skipped (empty)  {self.skipped_empty}",
            f"  changed on this run  {self.changed}",
            f"  cards with no locator      {self.locator_none}",
            f"  cards with estimated loc.  {self.locator_estimated}",
            f"  card placements read back  {self.placements_read}"
            + (f" ({self.still_in_inbox} still only in Inbox)"
               if self.still_in_inbox else ""),
        ])


def next_human_id(conn: sqlite3.Connection, project_id: int) -> Iterable[str]:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM card WHERE project_id = ?", (project_id,)
    ).fetchone()
    n = row["n"]
    while True:
        n += 1
        yield f"KJ-{n:04d}"


def build_annotation_index(client: ZoteroClient) -> tuple[dict[str, list[dict]], bool]:
    """
    Map attachment key -> its annotations, fetched in ONE request.

    The local API's /items/<key>/children returns child notes but NOT
    annotations, so per-attachment lookups come back empty. Querying
    itemType=annotation library-wide does return them, each carrying
    parentItem. This is also far cheaper: one request instead of one per
    attachment.

    Returns (index, ok). When ok is False the caller falls back to children().
    """
    index: dict[str, list[dict]] = {}
    try:
        annotations = client.items(itemType="annotation")
    except ZoteroError as e:
        log(f"  annotation index unavailable ({e}); falling back to per-item lookups")
        return {}, False
    for a in annotations:
        d = a.get("data", a)
        parent = d.get("parentItem")
        if parent:
            index.setdefault(parent, []).append(a)
    log(f"Annotation index: {len(annotations)} annotation(s) "
        f"across {len(index)} attachment(s)")
    return index, True


def run_import(
    project_name: str,
    root_key: str,
    use_google_books: bool = False,
    db_path: str = DB_PATH,
) -> ImportStats:
    client = ZoteroClient()
    info = client.server_info()
    conn = open_db(db_path)
    stats = ImportStats()

    log(f"Zotero API v{info.api_version or '?'} "
        f"server={info.server_id or 'n/a (pre-Zotero 10)'} "
        f"schema={info.schema_version or '?'}")

    nodes = build_tree(client.collections())
    keys = subtree_keys(nodes, root_key)
    log(f"Collection tree under {nodes[root_key].name}: {len(keys)} collection(s)")

    existing = conn.execute("SELECT * FROM project WHERE name = ?",
                            (project_name,)).fetchone()
    if existing and existing["root_collection_key"] != root_key:
        raise ZoteroError(
            f"Project {project_name!r} was built from collection "
            f"{existing['root_collection_key']}, and you are pointing it at "
            f"{root_key}. Mixing two collections into one project would merge "
            f"unrelated cards and corrupt the structure comparison.\n"
            f"Use a different --project name for this collection."
        )
    if existing and existing["zotero_server_id"] and \
            existing["zotero_server_id"] != info.server_id:
        raise ZoteroError(
            f"Project {project_name!r} came from Zotero database "
            f"{existing['zotero_server_id']}, but this Zotero is "
            f"{info.server_id}. Item keys and versions are not comparable "
            f"across databases. Use a separate --db file."
        )

    project_id = upsert(
        conn, "project",
        {"name": project_name},
        {"zotero_server_id": info.server_id,
         "root_collection_key": root_key,
         "created_at": existing["created_at"] if existing else now_iso(),
         "last_import_at": now_iso()},
    )
    # the researcher's prior structure -- subfolders as chapters
    coll_ids: dict[str, int] = {}
    for k in keys:
        n = nodes[k]
        coll_ids[k] = upsert(
            conn, "collection",
            {"project_id": project_id, "zotero_collection_key": k},
            {"parent_key": n.parent, "name": n.name, "path": n.path, "depth": n.depth},
        )

    ids = next_human_id(conn, project_id)
    epub_cache: dict[str, Optional[EpubIndex]] = {}
    ann_index, ann_index_ok = build_annotation_index(client)
    placed_seen: set = set()

    for ckey in keys:
        node = nodes[ckey]
        for item in client.collection_items_top(ckey):
            d = item.get("data", item)
            itype = d.get("itemType")

            # standalone notes living directly in a collection are ideas too
            if itype == "note":
                tags = {t.get("tag") for t in (d.get("tags") or [])}
                if KJ_TAG in tags:
                    # a card this tool created: read back where it was filed.
                    # A note in both Inbox and a theme is seen once per
                    # collection, so count the card, not the sighting.
                    unsorted_ = _record_placement(conn, project_id, d,
                                                  coll_ids, nodes)
                    if unsorted_ is not None and d.get("key") not in placed_seen:
                        placed_seen.add(d.get("key"))
                        stats.placements_read += 1
                        if unsorted_:
                            stats.still_in_inbox += 1
                    continue
                text = html_to_text(d.get("note"))
                if not text:
                    stats.skipped_empty += 1
                    continue
                _insert_card(
                    conn, project_id, ids, stats,
                    origin_key=f"note:{d['key']}",
                    kind="idea", origin="standalone_note",
                    text=text, text_raw=d.get("note"),
                    source_id=None, annotation_id=None,
                    # a standalone note can already be filed into collections,
                    # so it needs no separate KJ note
                    zotero_note_key=d["key"], origin_note_key=d["key"],
                    parent_card_id=None,
                    prior_collection_id=coll_ids[ckey], prior_path=node.path,
                    prior_ambiguous=0, kj_path=node.path,
                    locator=Locator("none", "", "none"), color=None,
                )
                continue

            if itype in ("attachment", "annotation"):
                continue

            source_id = upsert(
                conn, "source",
                {"project_id": project_id, "zotero_item_key": d["key"]},
                {"item_type": itype,
                 "title": d.get("title"),
                 "creators_json": json.dumps(d.get("creators") or [], ensure_ascii=False),
                 "creators_short": creators_short(d),
                 "year": first_year(d),
                 "publication_title": d.get("publicationTitle") or d.get("bookTitle"),
                 "doi": d.get("DOI"),
                 "isbn": d.get("ISBN"),
                 "url": d.get("url"),
                 "raw_json": json.dumps(item, ensure_ascii=False)},
            )
            stats.sources += 1
            conn.execute(
                "INSERT OR IGNORE INTO source_collection (source_id, collection_id) "
                "VALUES (?, ?)", (source_id, coll_ids[ckey])
            )
            prior_id, prior_path, ambiguous = _prior_structure(conn, source_id)

            for child in client.children(d["key"]):
                cd = child.get("data", child)
                ctype = cd.get("itemType")

                if ctype == "note":
                    text = html_to_text(cd.get("note"))
                    if not text:
                        stats.skipped_empty += 1
                        continue
                    _insert_card(
                        conn, project_id, ids, stats,
                        origin_key=f"note:{cd['key']}",
                        kind="idea", origin="child_note",
                        text=text, text_raw=cd.get("note"),
                        source_id=source_id, annotation_id=None,
                        # a child note hangs off its parent item and cannot be
                        # put in a collection, so it still needs a KJ note
                        zotero_note_key=None, origin_note_key=cd["key"],
                        parent_card_id=None,
                        prior_collection_id=prior_id, prior_path=prior_path,
                        prior_ambiguous=ambiguous,
                        locator=Locator("none", "", "none"), color=None,
                    )
                    continue

                if ctype != "attachment":
                    continue

                att_id = upsert(
                    conn, "attachment",
                    {"source_id": source_id, "zotero_attachment_key": cd["key"]},
                    {"content_type": cd.get("contentType"),
                     "title": cd.get("title"),
                     "filename": cd.get("filename"),
                     "link_mode": cd.get("linkMode"),
                     "raw_json": json.dumps(child, ensure_ascii=False)},
                )
                stats.attachments += 1

                is_epub = "epub" in (cd.get("contentType") or "").lower()
                epub_index = None
                page_count = None
                if is_epub:
                    stats.epub_attachments += 1
                    epub_index = _load_epub(client, cd["key"], epub_cache)
                    if use_google_books:
                        page_count = google_books_page_count(
                            conn, d.get("ISBN"), d.get("title"), creators_short(d)
                        )

                if ann_index_ok:
                    ann_items = ann_index.get(cd["key"], [])
                else:
                    ann_items = client.children(cd["key"])

                for ann_item in ann_items:
                    ad = ann_item.get("data", ann_item)
                    if ad.get("itemType") != "annotation":
                        continue
                    stats.annotations += 1
                    _handle_annotation(
                        conn, project_id, ids, stats, ad, ann_item, cd, att_id,
                        source_id, epub_index, page_count,
                        prior_id, prior_path, ambiguous,
                    )

    conn.execute("UPDATE project SET last_import_at = ? WHERE id = ?",
                 (now_iso(), project_id))
    conn.commit()
    conn.close()
    return stats


def _record_placement(conn: sqlite3.Connection, project_id: int, note_data: dict,
                      coll_ids: dict[str, int], nodes: dict[str, CollNode]
                      ) -> Optional[bool]:
    """
    A note carrying the kj-card tag is a card this tool created. Where the
    researcher dragged it IS the grouping decision, so read it back instead of
    importing the note as a fresh idea card.

    Returns None if the note is not one of ours, True if it is still sitting
    only in Inbox, False if it has been filed somewhere meaningful.
    """
    note_key = note_data.get("key")
    row = conn.execute(
        "SELECT id FROM card WHERE project_id = ? AND zotero_note_key = ?",
        (project_id, note_key),
    ).fetchone()
    if not row:
        return None
    keys = [k for k in (note_data.get("collections") or []) if k in nodes]
    # deepest collection wins; Inbox is a holding pen, not a grouping
    ranked = sorted(keys, key=lambda k: nodes[k].depth, reverse=True)
    meaningful = [k for k in ranked if nodes[k].name != "Inbox"]
    chosen = (meaningful or ranked or [None])[0]
    conn.execute(
        "UPDATE card SET kj_collection_keys_json = ?, kj_path = ? WHERE id = ?",
        (json.dumps(keys), nodes[chosen].path if chosen else None, row["id"]),
    )
    return not meaningful and bool(ranked)


def _load_epub(client: ZoteroClient, key: str,
               cache: dict[str, Optional[EpubIndex]]) -> Optional[EpubIndex]:
    if key in cache:
        return cache[key]
    idx = None
    url = client.file_url(key)
    if url and url.startswith("file://"):
        path = urllib.request.url2pathname(urllib.parse.urlparse(url).path)
        try:
            idx = EpubIndex(path)
        except Exception as e:
            log(f"  epub index failed for {key}: {e}")
    cache[key] = idx
    return idx


def _prior_structure(conn: sqlite3.Connection, source_id: int) -> tuple[Optional[int], Optional[str], int]:
    """Deepest collection the source sits in = the researcher's own outline slot."""
    rows = conn.execute(
        "SELECT c.id, c.path, c.depth FROM collection c "
        "JOIN source_collection sc ON sc.collection_id = c.id "
        "WHERE sc.source_id = ? ORDER BY c.depth DESC, c.path ASC", (source_id,)
    ).fetchall()
    if not rows:
        return None, None, 0
    deepest = rows[0]
    ambiguous = int(sum(1 for r in rows if r["depth"] == deepest["depth"]) > 1)
    return deepest["id"], deepest["path"], ambiguous


def _handle_annotation(conn, project_id, ids, stats, ad, ann_item, cd, att_id,
                       source_id, epub_index, page_count,
                       prior_id, prior_path, ambiguous) -> None:
    atype = ad.get("annotationType")
    text_raw = ad.get("annotationText") or ""
    comment_raw = ad.get("annotationComment") or ""
    color = ad.get("annotationColor")
    position = ad.get("annotationPosition")

    content_hash = sha256(text_raw, comment_raw, color,
                          ad.get("annotationPageLabel"),
                          ad.get("annotationSortIndex"), position)

    ann_id = upsert(
        conn, "annotation",
        {"attachment_id": att_id, "zotero_annotation_key": ad["key"]},
        {"annotation_type": atype,
         "text_raw": text_raw,
         "comment_raw": comment_raw,
         "color": color,
         "page_label": ad.get("annotationPageLabel"),
         "sort_index": ad.get("annotationSortIndex"),
         "position_json": position if isinstance(position, str)
                          else json.dumps(position, ensure_ascii=False),
         "date_modified": ad.get("dateModified"),
         "raw_json": json.dumps(ann_item, ensure_ascii=False),
         "content_hash": content_hash},
    )

    loc = resolve_locator(ad, cd, epub_index, page_count)

    quote_card_id = None
    if atype in NON_TEXTUAL_ANNOTATION_TYPES:
        quote_card_id = _insert_card(
            conn, project_id, ids, stats,
            origin_key=f"annotation:{ad['key']}:image",
            kind="image", origin="annotation_text",
            text=f"[{atype} annotation]", text_raw=None,
            source_id=source_id, annotation_id=ann_id,
            zotero_note_key=None, parent_card_id=None,
            prior_collection_id=prior_id, prior_path=prior_path,
            prior_ambiguous=ambiguous, locator=loc, color=color,
        )
    elif text_raw.strip():
        quote_card_id = _insert_card(
            conn, project_id, ids, stats,
            origin_key=f"annotation:{ad['key']}:quote",
            kind="quote", origin="annotation_text",
            text=normalise_quote(text_raw), text_raw=text_raw,
            source_id=source_id, annotation_id=ann_id,
            zotero_note_key=None, parent_card_id=None,
            prior_collection_id=prior_id, prior_path=prior_path,
            prior_ambiguous=ambiguous, locator=loc, color=color,
        )

    # The researcher's comment is an idea in its own right, not metadata
    # hanging off a quote. It gets its own card, linked to the quote.
    if comment_raw.strip():
        _insert_card(
            conn, project_id, ids, stats,
            origin_key=f"annotation:{ad['key']}:idea",
            kind="idea", origin="annotation_comment",
            text=comment_raw.strip(), text_raw=comment_raw,
            source_id=source_id, annotation_id=ann_id,
            zotero_note_key=None, parent_card_id=quote_card_id,
            prior_collection_id=prior_id, prior_path=prior_path,
            prior_ambiguous=ambiguous, locator=loc, color=color,
        )

    if not text_raw.strip() and not comment_raw.strip() \
            and atype not in NON_TEXTUAL_ANNOTATION_TYPES:
        stats.skipped_empty += 1


def _insert_card(conn, project_id, ids, stats, *, origin_key, kind, origin, text,
                 text_raw, source_id, annotation_id, zotero_note_key,
                 parent_card_id, prior_collection_id, prior_path,
                 prior_ambiguous, locator: Locator, color,
                 origin_note_key=None, kj_path=None) -> int:
    chash = sha256(kind, origin, text, locator.type, locator.value)
    row = conn.execute(
        "SELECT id, content_hash FROM card WHERE project_id = ? AND origin_key = ?",
        (project_id, origin_key)
    ).fetchone()

    values = {
        "kind": kind, "origin": origin, "text": text, "text_raw": text_raw,
        "source_id": source_id, "annotation_id": annotation_id,
        "zotero_note_key": zotero_note_key, "origin_note_key": origin_note_key,
        "parent_card_id": parent_card_id,
        "prior_collection_id": prior_collection_id, "prior_path": prior_path,
        "prior_ambiguous": prior_ambiguous,
        "locator_type": locator.type, "locator_value": locator.value,
        "locator_source": locator.source,
        "locator_estimated": int(locator.estimated),
        "locator_detail_json": json.dumps(locator.detail, ensure_ascii=False),
        "color": color, "content_hash": chash, "updated_at": now_iso(),
    }

    if row:
        if row["content_hash"] != chash:
            stats.changed += 1
            # never clobber a KJ note key we created with a NULL from re-import
            patch = dict(values)
            if patch.get("zotero_note_key") is None:
                patch.pop("zotero_note_key")
            sets = ", ".join(f"{k} = ?" for k in patch)
            conn.execute(f"UPDATE card SET {sets} WHERE id = ?",
                         tuple(patch.values()) + (row["id"],))
        return row["id"]

    # counted only on real insert; a card seen in two collections is one card
    if locator.type == "none":
        stats.locator_none += 1
    if locator.estimated:
        stats.locator_estimated += 1
    if kind == "quote":
        stats.quote_cards += 1
    elif kind == "idea":
        stats.idea_cards += 1
    else:
        stats.image_cards += 1

    human_id = next(ids)
    payload = {"project_id": project_id, "human_id": human_id,
               "origin_key": origin_key, "created_at": now_iso(),
               "kj_path": kj_path, **values}
    cols = ", ".join(payload)
    marks = ", ".join("?" for _ in payload)
    cur = conn.execute(f"INSERT INTO card ({cols}) VALUES ({marks})",
                       tuple(payload.values()))
    return cur.lastrowid


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

CARD_QUERY = """
SELECT c.*, s.creators_short, s.year, s.title AS source_title
FROM card c LEFT JOIN source s ON s.id = c.source_id
WHERE c.project_id = ?
"""


def fetch_cards(conn, project_id: int, kind: Optional[str] = None) -> list[sqlite3.Row]:
    q = CARD_QUERY
    params: list[Any] = [project_id]
    if kind:
        q += " AND c.kind = ?"
        params.append(kind)
    q += " ORDER BY c.prior_path, c.human_id"
    return conn.execute(q, params).fetchall()


def citation_string(row: sqlite3.Row) -> str:
    bits = " ".join(filter(None, [rowget(row, "creators_short"),
                                  rowget(row, "year")]))
    loc = Locator(rowget(row, "locator_type") or "none",
                  rowget(row, "locator_value") or "",
                  rowget(row, "locator_source") or "none",
                  bool(rowget(row, "locator_estimated"))).render()
    return ", ".join(filter(None, [bits, loc])) or "(no source)"


def get_project(conn, name: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM project WHERE name = ?", (name,)).fetchone()
    if not row:
        raise SystemExit(f"No project named {name!r}. Run `import` first.")
    return row


def cmd_cards(args) -> None:
    conn = open_db(args.db)
    project = get_project(conn, args.project)
    rows = fetch_cards(conn, project["id"], args.kind)

    lengths = sorted(len(r["text"]) for r in rows if r["kind"] != "image")
    if lengths:
        median = lengths[len(lengths) // 2]
        p90 = lengths[int(len(lengths) * 0.9)]
        log(f"{len(rows)} cards | median {median} chars | p90 {p90} chars "
            f"| longest {lengths[-1]} chars")
        long_share = sum(1 for n in lengths if n > 400) / len(lengths)
        log(f"{long_share:.0%} of cards are over 400 characters "
            f"-- these are passages, not cards; they will need splitting or labelling.")

    if args.export and args.export.endswith(".csv"):
        with open(args.export, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["human_id", "kind", "origin", "prior_path", "citation",
                        "locator_type", "locator_estimated", "text"])
            for r in rows:
                w.writerow([r["human_id"], r["kind"], r["origin"], r["prior_path"] or "",
                            citation_string(r), r["locator_type"],
                            r["locator_estimated"], r["text"]])
        log(f"wrote {args.export}")
    elif args.export:
        with open(args.export, "w", encoding="utf-8") as f:
            f.write(f"# {project['name']} -- cards\n\n")
            current = object()
            for r in rows:
                if r["prior_path"] != current:
                    current = r["prior_path"]
                    f.write(f"\n## {current or '(no collection)'}\n\n")
                marker = {"quote": ">", "idea": "*", "image": "-"}[r["kind"]]
                f.write(f"**{r['human_id']}** `{r['kind']}` -- {citation_string(r)}\n\n")
                for line in r["text"].splitlines() or [""]:
                    f.write(f"{marker} {line}\n")
                f.write("\n")
        log(f"wrote {args.export}")
    else:
        for r in rows[: args.limit]:
            print(f"{r['human_id']}  [{r['kind']:5}] {citation_string(r)}")
            print(f"    {r['text'][:180]}")
    conn.close()


# --------------------------------------------------------------------------
# structure comparison: your folders vs. what the text clusters into
# --------------------------------------------------------------------------

def cmd_compare(args) -> None:
    try:
        import numpy as np
        from sklearn.cluster import AgglomerativeClustering
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score
        from sklearn.preprocessing import normalize
    except ImportError:
        raise SystemExit("pip install scikit-learn")

    conn = open_db(args.db)
    project = get_project(conn, args.project)
    rows = [r for r in fetch_cards(conn, project["id"])
            if r["kind"] != "image" and r["prior_path"] and len(r["text"]) > 20]
    if len(rows) < 10:
        raise SystemExit("Not enough cards with a collection path to compare.")

    labels_prior = [r["prior_path"] for r in rows]
    groups = sorted(set(labels_prior))
    if len(groups) < 2:
        raise SystemExit("All cards sit in one collection -- nothing to compare.")
    k = args.k or len(groups)

    # char n-grams: works for Japanese, French and English without tokenisers
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4),
                          min_df=2, max_features=40000, sublinear_tf=True)
    X = normalize(vec.fit_transform([r["text"] for r in rows])).toarray()

    # Ward on L2-normalised vectors: euclidean distance is then monotone in
    # cosine distance, and ward avoids the chaining that average linkage
    # produces on sparse text, which otherwise yields one giant cluster.
    model = AgglomerativeClustering(n_clusters=k, linkage="ward")
    emergent = model.fit_predict(X)

    prior_idx = {g: i for i, g in enumerate(groups)}
    y_prior = [prior_idx[p] for p in labels_prior]

    ari = adjusted_rand_score(y_prior, emergent)
    nmi = normalized_mutual_info_score(y_prior, emergent)

    print(f"\nYour structure ({len(groups)} collections) vs. emergent structure "
          f"({k} clusters), {len(rows)} cards")
    print(f"  Adjusted Rand Index   {ari: .3f}")
    print(f"  Normalized Mutual Info{nmi: .3f}")
    print("  1.0 = the text falls exactly along your folders; "
          "0.0 = your folders and the text agree no more than chance.\n")

    # contingency
    table: dict[tuple[int, int], int] = {}
    for a, b in zip(y_prior, emergent):
        table[(a, b)] = table.get((a, b), 0) + 1
    width = max(len(g) for g in groups)
    print("  " + " " * width + "  " + "".join(f"{c:>6}" for c in range(k)))
    for gi, g in enumerate(groups):
        line = "".join(f"{table.get((gi, c), 0):>6}" for c in range(k))
        print(f"  {g:<{width}}  {line}")

    sizes = [int((emergent == c).sum()) for c in range(k)]
    if max(sizes) > 0.7 * len(rows):
        print("\n  WARNING: one cluster holds most cards. The texts are probably "
              "too uniform in vocabulary for this crude method; treat the "
              "numbers above as unreliable.")

    # where each emergent cluster mostly lives
    home: dict[int, int] = {}
    for c in range(k):
        counts = {gi: table.get((gi, c), 0) for gi in range(len(groups))}
        home[c] = max(counts, key=counts.get)

    print("\n  Emergent clusters, nearest cards to each centroid:\n")
    for c in range(k):
        idx = np.where(emergent == c)[0]
        centroid = X[idx].mean(axis=0)
        order = idx[np.argsort(-(X[idx] @ centroid))][:3]
        print(f"  cluster {c} ({sizes[c]} cards, mostly {groups[home[c]]})")
        for i in order:
            print(f"      {rows[i]['human_id']} {rows[i]['text'][:110]}")
        print()

    print("\n  Cards whose text sits with a different chapter than the one "
          "you filed them under:\n")
    misfits = 0
    for r, c in zip(rows, emergent):
        if home[c] != prior_idx[r["prior_path"]]:
            misfits += 1
            if misfits <= args.show:
                print(f"  {r['human_id']} [{r['kind']}] filed in: {r['prior_path']}")
                print(f"      clusters with: {groups[home[c]]}")
                print(f"      {r['text'][:150]}\n")
    print(f"  {misfits} of {len(rows)} cards ({misfits/len(rows):.0%}) "
          f"pull against your outline.")
    print("  These are the ones worth re-reading. A misfit is not an error -- "
          "it is either a mis-filing or a sign the chapter boundary is wrong.\n")
    conn.close()


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_check(args) -> None:
    client = ZoteroClient()
    info = client.server_info()
    print(f"Zotero reachable at {ZOTERO_BASE}")
    print(f"  API version    {info.api_version or 'unknown'}")
    print(f"  Server ID      {info.server_id or 'absent -- Zotero 9 or older'}")
    print(f"  Schema version {info.schema_version or 'unknown'}")
    colls = client.collections()
    print(f"  Collections    {len(colls)}")
    if not info.server_id:
        print("\n  Note: no Zotero-Server-ID header. Local API writes need "
              "Zotero 10+ (released 2026-08-17). v0 is read-only, so this is "
              "fine for now, but note-writing will need an upgrade.")


def cmd_collections(args) -> None:
    client = ZoteroClient()
    nodes = build_tree(client.collections())
    roots = [n for n in nodes.values() if not n.parent or n.parent not in nodes]

    def show(n: CollNode, indent: int = 0) -> None:
        print(f"{'  ' * indent}{n.key}  {n.name}")
        for ch in sorted(n.children, key=lambda x: x.name):
            show(ch, indent + 1)

    for r in sorted(roots, key=lambda x: x.name):
        show(r)


KJ_TAG = "kj-card"


def rowget(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    """sqlite3.Row raises on unknown keys; tolerate rows from narrower queries."""
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def esc(s: str) -> str:
    return html.escape(s or "", quote=False).replace("\n", "<br/>")


def render_note(card: sqlite3.Row, project_name: str,
                parent_human_id: Optional[str]) -> str:
    """
    Standalone-note HTML for one card.

    The trailing marker block is what lets a re-import recognise this note as
    a card rather than treating it as a new idea, and it survives the note
    being dragged anywhere in Zotero.
    """
    kind = card["kind"]
    head = f"{card['human_id']} · " + (
        "label" if rowget(card, "origin") == "group_label" else kind)
    parts = [f"<h2>{esc(head)}</h2>"]

    if kind == "quote":
        parts.append(f"<blockquote>{esc(card['text'])}</blockquote>")
    elif rowget(card, "origin") == "group_label":
        body = card["text"].split("\n\n", 1)
        parts.append(f"<p><strong>{esc(body[0])}</strong></p>")
        if len(body) > 1:
            parts.append(f"<p>{esc(body[1])}</p>")
        if rowget(card, "kj_path"):
            parts.append(f"<p><em>Label for: {esc(card['kj_path'])}</em></p>")
    else:
        parts.append(f"<p>{esc(card['text'])}</p>")

    cite = citation_string(card)
    if cite and cite != "(no source)":
        parts.append(f"<p><strong>Source:</strong> {esc(cite)}</p>")
    if rowget(card, "source_title"):
        parts.append(f"<p><em>{esc(rowget(card, 'source_title'))}</em></p>")
    if rowget(card, "locator_estimated"):
        parts.append("<p><em>Locator is estimated — verify before citing.</em></p>")
    if parent_human_id:
        parts.append(f"<p><strong>My reading of:</strong> {esc(parent_human_id)}</p>")

    parts.append("<hr/>")
    parts.append(
        "<p>"
        f"kj:card={esc(card['human_id'])} "
        f"kj:kind={esc(kind)} "
        f"kj:project={esc(project_name)} "
        f"kj:origin={esc(card['origin_key'])}"
        "</p>"
    )
    return "".join(parts)


def target_collection(card: sqlite3.Row, coll_keys: dict[str, str],
                      inbox_key: str) -> str:
    """
    A group label belongs in the collection it names; everything else starts
    in Inbox so the researcher decides where it goes.
    """
    if rowget(card, "origin") == "group_label":
        return coll_keys.get(rowget(card, "kj_path"), inbox_key)
    return inbox_key


def note_payload(card: sqlite3.Row, project_name: str,
                 parent_human_id: Optional[str], collection_key: str) -> dict:
    return {
        "itemType": "note",
        "note": render_note(card, project_name, parent_human_id),
        "tags": [{"tag": KJ_TAG},
                 {"tag": f"kj-kind:{card['kind']}"},
                 {"tag": f"kj-project:{project_name}"}],
        "collections": [collection_key],
    }


class WriteSession:
    """
    Holds a local API write key and re-authorizes when Zotero consumes it.

    A key granted with "Allow" is single-use, and Zotero accepts at most five
    dialog-showing requests per minute, so a run that needs many keys has to
    pace itself. "Always Allow" avoids all of this.
    """

    def __init__(self, client: ZoteroClient, conn: sqlite3.Connection):
        self.client = client
        self.conn = conn
        self.server_id = client.server_info().server_id or ""
        self.key: Optional[str] = None
        self.remember = False
        self._last_auth = 0.0
        self._load()

    def _load(self) -> None:
        row = self.conn.execute(
            "SELECT * FROM write_auth WHERE server_id = ?", (self.server_id,)
        ).fetchone()
        if row and row["remember"]:
            self.key = row["api_key"]
            self.remember = True

    def _store(self) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO write_auth "
            "(server_id, api_key, remember, granted_at) VALUES (?, ?, ?, ?)",
            (self.server_id, self.key, int(self.remember), now_iso()),
        )
        self.conn.commit()

    def acquire(self, force: bool = False) -> str:
        if self.key and not force:
            return self.key
        gap = time.time() - self._last_auth
        if self._last_auth and gap < 13:
            wait = 13 - gap
            log(f"  pausing {wait:.0f}s to stay under Zotero's prompt rate limit")
            time.sleep(wait)
        log("  requesting write permission -- approve the dialog in Zotero")
        data = self.client.authorize_write()
        self.key = data.get("key")
        self.remember = bool(data.get("remember"))
        self._last_auth = time.time()
        if not self.key:
            raise ZoteroError("Zotero returned no write key.")
        if self.remember:
            self._store()
            log("  'Always Allow' granted -- key stored, no further prompts")
        else:
            log("  single-use key granted; each batch will prompt again")
        return self.key

    def run(self, fn):
        """Call fn(api_key), re-authorizing once if the key was consumed."""
        key = self.acquire()
        try:
            return fn(key)
        except ZoteroWriteError as e:
            if e.status != 401:
                raise
            self.key = None if not self.remember else self.key
            key = self.acquire(force=True)
            return fn(key)

    def invalidate_if_single_use(self) -> None:
        if not self.remember:
            self.key = None


def ensure_kj_collections(client: ZoteroClient, session: WriteSession,
                          conn: sqlite3.Connection, project: sqlite3.Row
                          ) -> str:
    """Create _KJ and _KJ/Inbox under the project root if they are missing."""
    if project["kj_inbox_key"]:
        return project["kj_inbox_key"]

    nodes = build_tree(client.collections())
    root_key = project["root_collection_key"]
    by_parent_name = {(n.parent, n.name): n.key for n in nodes.values()}

    kj_key = by_parent_name.get((root_key, "_KJ"))
    if not kj_key:
        res = session.run(lambda k: client.create_collections(
            [{"name": "_KJ", "parentCollection": root_key}], k))
        session.invalidate_if_single_use()
        ok, errs = parse_write_result(res, 1)
        if errs:
            raise ZoteroError(f"Could not create _KJ collection: {errs}")
        kj_key = ok[0]
        log(f"  created collection _KJ ({kj_key})")

    inbox_key = by_parent_name.get((kj_key, "Inbox"))
    if not inbox_key:
        res = session.run(lambda k: client.create_collections(
            [{"name": "Inbox", "parentCollection": kj_key}], k))
        session.invalidate_if_single_use()
        ok, errs = parse_write_result(res, 1)
        if errs:
            raise ZoteroError(f"Could not create Inbox collection: {errs}")
        inbox_key = ok[0]
        log(f"  created collection _KJ/Inbox ({inbox_key})")

    conn.execute("UPDATE project SET kj_root_key = ?, kj_inbox_key = ? WHERE id = ?",
                 (kj_key, inbox_key, project["id"]))
    conn.commit()
    return inbox_key


def cmd_authorize(args) -> None:
    client = ZoteroClient()
    conn = open_db(args.db)
    session = WriteSession(client, conn)
    if session.key and session.remember and not args.force:
        print("A stored 'Always Allow' key is already present for this Zotero "
              "database. Use --force to request a new one.")
        return
    print("Zotero will show a dialog. Choose 'Always Allow' -- with plain "
          "'Allow' the key is consumed by the first write and every further "
          "batch prompts again (max five prompts per minute).")
    session.acquire(force=True)
    if not session.remember:
        print("\nYou chose 'Allow'. That works, but expect one prompt per "
              "batch of 50 cards.")
    conn.close()


def cmd_materialize(args) -> None:
    client = ZoteroClient()
    conn = open_db(args.db)
    project = get_project(conn, args.project)

    server_id = client.server_info().server_id
    if project["zotero_server_id"] and server_id != project["zotero_server_id"]:
        raise SystemExit(
            f"This project was imported from Zotero database "
            f"{project['zotero_server_id']}, but the running Zotero is "
            f"{server_id}. Refusing to write. Re-import first."
        )

    kinds = args.kind or ["quote", "idea"]
    marks = ",".join("?" for _ in kinds)
    rows = conn.execute(
        CARD_QUERY + f" AND c.kind IN ({marks}) AND c.zotero_note_key IS NULL "
        "AND c.status != 'excluded' ORDER BY c.human_id",
        [project["id"], *kinds],
    ).fetchall()
    if args.limit:
        rows = rows[: args.limit]

    if not rows:
        total = conn.execute(
            "SELECT kind, COUNT(*) n, "
            "SUM(zotero_note_key IS NOT NULL) noted, "
            "SUM(status = 'excluded') excluded "
            "FROM card WHERE project_id = ? GROUP BY kind",
            (project["id"],)).fetchall()
        if not total:
            print(f"Project {project['name']!r} has no cards at all. "
                  f"Run `import` first -- and check it did not fail partway.")
        else:
            print("Nothing left to materialise. Current cards:")
            for t in total:
                print(f"  {t['kind']:6} {t['n']:4}  "
                      f"already noted: {t['noted'] or 0}  "
                      f"excluded: {t['excluded'] or 0}")
            print(f"\n(asked for kinds: {', '.join(kinds)})")
        return

    print(f"{len(rows)} card(s) to materialise as standalone Zotero notes "
          f"({', '.join(kinds)}).")
    if args.dry_run:
        for r in rows[:5]:
            print(f"\n--- {r['human_id']} ---")
            print(render_note(r, project["name"], None)[:600])
        print(f"\n(dry run; {len(rows)} notes would be created)")
        return

    session = WriteSession(client, conn)
    inbox_key = ensure_kj_collections(client, session, conn, project)

    # a comment card names the quote it responds to
    parent_ids = {r["id"]: r["human_id"] for r in fetch_cards(conn, project["id"])}
    coll_keys = {
        r["path"]: r["zotero_collection_key"] for r in conn.execute(
            "SELECT path, zotero_collection_key FROM collection "
            "WHERE project_id = ?", (project["id"],))
    }

    created = 0
    placed: dict[str, int] = {}
    failed: list[str] = []
    for start in range(0, len(rows), WRITE_BATCH):
        batch = rows[start:start + WRITE_BATCH]
        payload = [
            note_payload(r, project["name"],
                         parent_ids.get(r["parent_card_id"]),
                         target_collection(r, coll_keys, inbox_key))
            for r in batch
        ]
        log(f"  writing cards {start + 1}-{start + len(batch)} of {len(rows)}")
        res = session.run(lambda k, p=payload: client.create_items(p, k))
        session.invalidate_if_single_use()
        ok, errs = parse_write_result(res, len(batch))
        for idx, note_key in ok.items():
            card = batch[idx]
            dest = ("_KJ/Inbox" if target_collection(card, coll_keys, inbox_key)
                    == inbox_key else rowget(card, "kj_path") or "?")
            placed[dest] = placed.get(dest, 0) + 1
            conn.execute(
                "UPDATE card SET zotero_note_key = ?, materialized_at = ?, "
                "kj_path = COALESCE(kj_path, ?) WHERE id = ?",
                (note_key, now_iso(), "_KJ/Inbox", card["id"]),
            )
            created += 1
        for idx, err in errs.items():
            failed.append(f"{batch[idx]['human_id']}: {err}")
        conn.commit()

    print(f"\ncreated {created} note(s):")
    for dest, n in sorted(placed.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>4}  {dest}")
    if failed:
        print(f"failed: {len(failed)}")
        for f in failed[:10]:
            print(f"  {f}")
    if placed.get("_KJ/Inbox"):
        print("\nNow open Zotero. Create subcollections under _KJ for whatever "
              "groupings you see, and drag the notes into them. Then run "
              "`import` again -- where you put each card is read back as your "
              "grouping.")
    else:
        print("\nEach label is filed with the group it names. Open Zotero to "
              "read them next to their evidence.")
    conn.close()


# --------------------------------------------------------------------------
# groups: turn "which quotes I put together" into a written proposition
# --------------------------------------------------------------------------

LABEL_RE = re.compile(r"^LABEL:\s*(.*)$")
NOTE_RE = re.compile(r"^NOTE:\s*(.*)$")
GROUP_RE = re.compile(r"^##\s+(.*)$")


def group_cards(conn: sqlite3.Connection, project_id: int
                ) -> dict[str, list[sqlite3.Row]]:
    """Quote/image cards keyed by the collection the researcher put them in."""
    rows = conn.execute(
        CARD_QUERY + " AND c.kj_path IS NOT NULL AND c.origin != 'group_label' "
        "ORDER BY c.kj_path, c.human_id", (project_id,)).fetchall()
    out: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        out.setdefault(r["kj_path"], []).append(r)
    # a group worth labelling holds evidence, or holds more than one card;
    # a single standalone note filed somewhere is already its own statement
    return {
        path: cards for path, cards in out.items()
        if len(cards) > 1 or any(c["kind"] in ("quote", "image") for c in cards)
    }


def most_dissimilar_pair(cards: list[sqlite3.Row]) -> Optional[tuple[str, str]]:
    """
    The two cards in a group that share the least vocabulary.

    A crude proxy for tension: if you grouped these together despite them
    having little in common on the surface, the reason you did is exactly what
    the label needs to say.
    """
    if len(cards) < 3:
        return None
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.preprocessing import normalize
    except ImportError:
        return None
    texts = [c["text"] for c in cards]
    try:
        X = normalize(TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4),
                                      min_df=1).fit_transform(texts)).toarray()
    except ValueError:
        return None
    worst, pair = 2.0, None
    for i in range(len(cards)):
        for j in range(i + 1, len(cards)):
            sim = float(X[i] @ X[j])
            if sim < worst:
                worst, pair = sim, (cards[i]["human_id"], cards[j]["human_id"])
    return pair


def cmd_groups(args) -> None:
    conn = open_db(args.db)
    project = get_project(conn, args.project)
    if args.import_file:
        _groups_import(conn, project, args.import_file)
        conn.close()
        return

    groups = group_cards(conn, project["id"])
    if not groups:
        raise SystemExit(
            "No grouped cards yet. Materialise the cards, drag them into "
            "subcollections under _KJ in Zotero, then run `import` again."
        )
    existing = {
        r["kj_path"]: r for r in conn.execute(
            "SELECT * FROM card WHERE project_id = ? AND origin = 'group_label'",
            (project["id"],)).fetchall()
    }

    lines = [f"# {project['name']} — group worksheet", ""]
    lines += [
        "<!-- Write one sentence per group on the LABEL line: a proposition,",
        "     not a topic. 'Competition' is a heading; 'The competition frame",
        "     borrows its urgency from security language' is a label.",
        "     NOTE is optional and can run longer.",
        "     Then: python zkj_v0.py groups --project "
        f"{project['name']} --import {os.path.basename(args.export or 'groups.md')}",
        "-->", "",
    ]
    for path, cards in groups.items():
        prev = existing.get(path)
        label = ""
        note = ""
        if prev:
            body = prev["text"].split("\n\n", 1)
            label = body[0]
            note = body[1] if len(body) > 1 else ""
        lines += [f"## {path}", f"LABEL: {label}", f"NOTE: {note}", ""]
        pair = most_dissimilar_pair(cards)
        if pair:
            lines.append(f"<!-- least alike in this group: {pair[0]} and "
                         f"{pair[1]} — why did they end up together? -->")
        lines.append(f"<!-- {len(cards)} card(s) -->")
        lines.append("")
        for c in cards:
            lines.append(f"- **{c['human_id']}** ({citation_string(c)})")
            lines.append(f"  {c['text']}")
            lines.append("")
        lines.append("")

    target = args.export or "groups.md"
    with open(target, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    filled = sum(1 for p in groups if p in existing)
    print(f"wrote {target}: {len(groups)} group(s), {sum(len(v) for v in groups.values())} cards"
          f"{f', {filled} label(s) already written' if filled else ''}")
    print("Fill in the LABEL lines, then re-run with --import.")
    conn.close()


def _groups_import(conn: sqlite3.Connection, project: sqlite3.Row,
                   path: str) -> None:
    if not os.path.exists(path):
        raise SystemExit(f"No such file: {path}")
    parsed: dict[str, dict[str, str]] = {}
    current = None
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        m = GROUP_RE.match(line)
        if m:
            current = m.group(1).strip()
            parsed[current] = {"label": "", "note": ""}
            continue
        if current is None:
            continue
        m = LABEL_RE.match(line)
        if m:
            parsed[current]["label"] = m.group(1).strip()
            continue
        m = NOTE_RE.match(line)
        if m:
            parsed[current]["note"] = m.group(1).strip()

    groups = group_cards(conn, project["id"])
    ids = next_human_id(conn, project["id"])
    stats = ImportStats()
    written = skipped = 0
    for gpath, fields in parsed.items():
        if not fields["label"]:
            skipped += 1
            continue
        if gpath not in groups:
            log(f"  no such group, ignoring: {gpath}")
            continue
        text = fields["label"]
        if fields["note"]:
            text += "\n\n" + fields["note"]
        coll = conn.execute(
            "SELECT id FROM collection WHERE project_id = ? AND path = ?",
            (project["id"], gpath)).fetchone()
        _insert_card(
            conn, project["id"], ids, stats,
            origin_key=f"group:{gpath}",
            kind="idea", origin="group_label",
            text=text, text_raw=None,
            source_id=None, annotation_id=None,
            zotero_note_key=None, origin_note_key=None,
            parent_card_id=None,
            prior_collection_id=coll["id"] if coll else None,
            prior_path=gpath, prior_ambiguous=0, kj_path=gpath,
            locator=Locator("none", "", "none"), color=None,
        )
        written += 1
    conn.commit()
    print(f"{written} label(s) saved as idea cards"
          f"{f', {skipped} group(s) left blank' if skipped else ''}")
    if written:
        print("Run `materialize` to push them into Zotero as notes filed in "
              "their own subcollection.")


def cmd_status(args) -> None:
    """What is actually in the local database. No Zotero connection needed."""
    conn = open_db(args.db)
    projects = conn.execute("SELECT * FROM project ORDER BY name").fetchall()
    if not projects:
        print(f"{args.db}: no projects yet.")
        return
    for p in projects:
        print(f"\nproject {p['name']!r}")
        print(f"  root collection  {p['root_collection_key']}")
        print(f"  zotero server    {p['zotero_server_id'] or '-'}")
        print(f"  last import      {p['last_import_at'] or '-'}")
        print(f"  _KJ inbox        {p['kj_inbox_key'] or '(not created yet)'}")
        counts = conn.execute(
            "SELECT kind, COUNT(*) n, SUM(zotero_note_key IS NOT NULL) noted, "
            "SUM(kj_path IS NOT NULL) placed FROM card WHERE project_id = ? "
            "GROUP BY kind", (p["id"],)).fetchall()
        if not counts:
            print("  cards            none")
            continue
        print(f"  {'kind':8}{'cards':>7}{'as notes':>10}{'filed':>8}")
        for c in counts:
            print(f"  {c['kind']:8}{c['n']:>7}{c['noted'] or 0:>10}"
                  f"{c['placed'] or 0:>8}")
        paths = conn.execute(
            "SELECT kj_path, COUNT(*) n FROM card WHERE project_id = ? "
            "AND kj_path IS NOT NULL GROUP BY kj_path ORDER BY n DESC",
            (p["id"],)).fetchall()
        if paths:
            print("  where you filed them:")
            for row in paths:
                print(f"    {row['n']:>4}  {row['kj_path']}")
    conn.close()


def cmd_diagnose(args) -> None:
    """
    Work out why an import found no annotations.

    Distinguishes 'this collection has no highlights' from 'the API is not
    handing annotations over the way this script asks for them'.
    """
    client = ZoteroClient()
    client.server_info()

    print("=== 1. Does this library contain annotations at all? ===")
    lib_annots: list[dict] = []
    try:
        lib_annots = client.items(itemType="annotation")
        print(f"  library-wide annotations: {len(lib_annots)}")
    except ZoteroError as e:
        print(f"  query failed: {e}")
    if lib_annots:
        sample = lib_annots[0].get("data", lib_annots[0])
        print(f"  sample key={sample.get('key')} "
              f"type={sample.get('annotationType')} "
              f"parent={sample.get('parentItem')} "
              f"textlen={len(sample.get('annotationText') or '')}")
        parents = {(a.get('data', a) or {}).get('parentItem') for a in lib_annots}
        print(f"  spread across {len(parents)} attachment(s)")
    else:
        print("  -> no annotations anywhere in the library, or the itemType "
              "filter is not supported here.")

    print("\n=== 2. What does this collection actually contain? ===")
    nodes = build_tree(client.collections())
    keys = subtree_keys(nodes, args.collection)
    print(f"  {len(keys)} collection(s) under {nodes[args.collection].name}")

    attachments: list[tuple[str, dict, str]] = []
    kinds: dict[str, int] = {}
    for ckey in keys:
        for item in client.collection_items_top(ckey):
            d = item.get("data", item)
            kinds[d.get("itemType", "?")] = kinds.get(d.get("itemType", "?"), 0) + 1
            if d.get("itemType") in ("attachment", "note"):
                continue
            for child in client.children(d["key"]):
                cd = child.get("data", child)
                if cd.get("itemType") == "attachment":
                    attachments.append((cd["key"], cd, d.get("title") or ""))
    print(f"  top-level item types: {kinds}")
    print(f"  attachments found: {len(attachments)}")

    by_ct: dict[str, int] = {}
    by_lm: dict[str, int] = {}
    for _k, cd, _t in attachments:
        by_ct[cd.get("contentType") or "(none)"] = by_ct.get(cd.get("contentType") or "(none)", 0) + 1
        by_lm[cd.get("linkMode") or "(none)"] = by_lm.get(cd.get("linkMode") or "(none)", 0) + 1
    print(f"  by content type: {by_ct}")
    print(f"  by link mode:    {by_lm}")
    print("  (linked_url attachments are bookmarks with no file, so they can "
          "never carry annotations)")

    print("\n=== 3. Children of attachments ===")
    annot_parents: set = set()
    if lib_annots:
        annot_parents = {(a.get('data', a) or {}).get('parentItem') for a in lib_annots}
    hits = [(k, cd, t) for k, cd, t in attachments if k in annot_parents]
    print(f"  attachments in this collection that own annotations "
          f"(per step 1): {len(hits)}")

    children_works = False
    # inspect the annotated ones FIRST -- the others tell us nothing
    for key, cd, title in (hits + [a for a in attachments if a not in hits])[: args.n]:
        try:
            kids = client.children(key)
        except ZoteroError as e:
            print(f"  {key}: children request failed: {e}")
            continue
        types: dict[str, int] = {}
        for kid in kids:
            t = (kid.get("data", kid) or {}).get("itemType", "?")
            types[t] = types.get(t, 0) + 1
        if key in annot_parents and types.get("annotation"):
            children_works = True
        flag = " <-- owns annotations" if key in annot_parents else ""
        print(f"  {key} [{cd.get('contentType')}] {title[:40]!r} "
              f"-> {len(kids)} child item(s) {types or '{}'}{flag}")

    print("\n=== verdict ===")
    if not lib_annots:
        print("  No annotations exist in the library (or none are exposed). "
              "Highlight a few passages in Zotero's reader, then re-run.")
    elif not hits:
        print("  Annotations exist elsewhere in your library, but none of them "
              "belong to attachments in THIS collection. Try importing a "
              "collection you have actually read through.")
    elif children_works:
        print("  children() does return annotations here, so an empty import "
              "has some other cause. Send this output back.")
    else:
        print(f"  {len(hits)} attachment(s) here own annotations, but "
              "children() returns none of them -- the local API does not "
              "expose annotations as child items. The import now uses the "
              "library-wide annotation index instead; re-run `import`.")


def cmd_import(args) -> None:
    stats = run_import(args.project, args.collection,
                       use_google_books=args.google_books, db_path=args.db)
    print(f"\nImported into {args.db!r} as project {args.project!r}:\n")
    print(stats.render())
    print("\n('new' counts only cards created on this run; "
          "run `status` for project totals)")
    print("\nNext: `cards --export cards.md` and read fifty of them. "
          "If they do not read as usable research material, stop here -- "
          "the premise is wrong and no amount of frontend fixes it.\n")


# --------------------------------------------------------------------------
# offline self-test
# --------------------------------------------------------------------------

FIXTURE = {
    "collections": [
        {"data": {"key": "ROOT", "name": "Agentic Governance", "parentCollection": False}},
        {"data": {"key": "CH02", "name": "02 Oversight", "parentCollection": "ROOT"}},
        {"data": {"key": "CH03", "name": "03 Capacity", "parentCollection": "ROOT"}},
    ],
    "items": {
        "CH02": [
            {"data": {"key": "SRC1", "itemType": "journalArticle",
                      "title": "Human oversight of autonomous agents",
                      "creators": [{"creatorType": "author", "lastName": "Smith"}],
                      "date": "2025-04-01", "publicationTitle": "AI & Society"}},
            {"data": {"key": "NOTE9", "itemType": "note",
                      "note": "<p>My own framing: oversight is an "
                              "organisational property, not an individual one.</p>"}},
        ],
        "CH03": [
            {"data": {"key": "SRC2", "itemType": "book",
                      "title": "State capacity and regulation",
                      "creators": [{"creatorType": "author", "lastName": "Tanaka"}],
                      "date": "2024", "ISBN": "9780000000000"}},
        ],
    },
    "children": {
        "SRC1": [
            {"data": {"key": "ATT1", "itemType": "attachment",
                      "contentType": "application/pdf", "title": "PDF",
                      "linkMode": "imported_file"}},
            {"data": {"key": "NOTE1", "itemType": "note",
                      "note": "<p>Smith conflates oversight with auditability.</p>"}},
        ],
        "SRC2": [
            {"data": {"key": "ATT2", "itemType": "attachment",
                      "contentType": "application/epub+zip", "title": "EPUB",
                      "linkMode": "imported_file"}},
        ],
        "ATT1": [
            {"data": {"key": "ANN1", "itemType": "annotation", "parentItem": "ATT1",
                      "annotationType": "highlight",
                      "annotationText": "Human oversight becomes increasingly dif-\nficult as autonomous agents operate across organizational boundaries.",
                      "annotationComment": "This is the hinge of my argument in ch.2.",
                      "annotationColor": "#ffd400",
                      "annotationPageLabel": "132",
                      "annotationSortIndex": "00132|000000|00000",
                      "annotationPosition": {"pageIndex": 131, "rects": []},
                      "dateModified": "2026-08-01T00:00:00Z"}},
            {"data": {"key": "ANN2", "itemType": "annotation", "parentItem": "ATT1",
                      "annotationType": "image",
                      "annotationPageLabel": "140",
                      "annotationPosition": {"pageIndex": 139}}},
            {"data": {"key": "ANN3", "itemType": "annotation", "parentItem": "ATT1",
                      "annotationType": "highlight",
                      "annotationText": "",
                      "annotationComment": "",
                      "annotationPosition": {"pageIndex": 8}}},
        ],
        "ATT2": [
            {"data": {"key": "ANN4", "itemType": "annotation", "parentItem": "ATT2",
                      "annotationType": "highlight",
                      "annotationText": "Regulatory capacity lags behind deployment.",
                      "annotationComment": "",
                      "annotationPageLabel": "",
                      "annotationPosition": {
                          "type": "FragmentSelector",
                          "value": "epubcfi(/6/14[chap05]!/4/10/2/1:3)"}}},
        ],
    },
}


class FakeClient(ZoteroClient):
    def __init__(self):
        super().__init__()
        self._server = ServerInfo("3", "TESTSERVER01", "999")

    def collections(self): return FIXTURE["collections"]
    def collection_items_top(self, key): return FIXTURE["items"].get(key, [])
    def children(self, key):
        # mirrors the real local API: child notes yes, annotations no
        return [c for c in FIXTURE["children"].get(key, [])
                if c["data"].get("itemType") != "annotation"]

    def items(self, **params):
        if params.get("itemType") != "annotation":
            return []
        out = []
        for kids in FIXTURE["children"].values():
            out += [c for c in kids if c["data"].get("itemType") == "annotation"]
        return out

    def file_url(self, key): return None


def cmd_selftest(args) -> None:
    import tempfile
    global ZoteroClient  # noqa: PLW0603
    real = ZoteroClient
    path = os.path.join(tempfile.mkdtemp(), "selftest.sqlite3")
    try:
        globals()["ZoteroClient"] = FakeClient
        stats = run_import("selftest", "ROOT", use_google_books=False, db_path=path)
    finally:
        globals()["ZoteroClient"] = real

    conn = open_db(path)
    pid = get_project(conn, "selftest")["id"]
    rows = fetch_cards(conn, pid)
    by_kind = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r)

    failures = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)

    check(len(by_kind.get("quote", [])) == 2, "expected 2 quote cards")
    check(len(by_kind.get("idea", [])) == 3,
          "expected 3 idea cards (1 comment + 1 child note + 1 standalone)")
    check(len(by_kind.get("image", [])) == 1, "expected 1 image card")
    check(stats.skipped_empty == 1, "expected 1 empty annotation skipped")

    q1 = next(r for r in rows if r["origin_key"] == "annotation:ANN1:quote")
    check("dif-\nficult" not in q1["text"] and "difficult" in q1["text"],
          "hyphenated line break not repaired")
    check(q1["text_raw"] and "dif-" in q1["text_raw"], "raw text not preserved")
    check(q1["locator_type"] == "page" and q1["locator_value"] == "132",
          "PDF page label not used")
    check(q1["prior_path"] == "Agentic Governance/02 Oversight",
          f"prior path wrong: {q1['prior_path']}")

    idea = next(r for r in rows if r["origin_key"] == "annotation:ANN1:idea")
    check(idea["kind"] == "idea" and idea["parent_card_id"] == q1["id"],
          "comment card not linked to its quote card")
    check(idea["locator_value"] == "132", "idea card lost its locator")

    epub = next(r for r in rows if r["origin_key"] == "annotation:ANN4:quote")
    check(epub["locator_type"] == "cfi" and epub["locator_estimated"] == 1,
          f"EPUB fallback wrong: {epub['locator_type']}")
    check(cfi_spine_index("epubcfi(/6/14[chap05]!/4/10/2/1:3)") == 6,
          "CFI spine index parsing wrong")

    standalone = next(r for r in rows if r["origin"] == "standalone_note")
    check(standalone["source_id"] is None and standalone["kind"] == "idea",
          "standalone note should be a sourceless idea card")
    check(standalone["zotero_note_key"] == "NOTE9",
          "a standalone note is already filable and needs no KJ note")

    child = next(r for r in rows if r["origin"] == "child_note")
    check(child["zotero_note_key"] is None and child["origin_note_key"] == "NOTE1",
          "a child note cannot be filed, so it must still await materialising")

    quote_card = next(r for r in rows if r["origin_key"] == "annotation:ANN1:quote")
    check(quote_card["zotero_note_key"] is None,
          "an annotation card has no note until materialised")

    # idempotency
    globals()["ZoteroClient"] = FakeClient
    try:
        run_import("selftest", "ROOT", use_google_books=False, db_path=path)
    finally:
        globals()["ZoteroClient"] = real
    conn2 = open_db(path)
    n2 = len(fetch_cards(conn2, get_project(conn2, "selftest")["id"]))
    check(n2 == len(rows), f"re-import created duplicates: {len(rows)} -> {n2}")

    # per-project uniqueness of an annotation key
    try:
        globals()["ZoteroClient"] = FakeClient
        run_import("selftest-2", "ROOT", use_google_books=False, db_path=path)
    except sqlite3.IntegrityError as e:
        failures.append(f"same annotation in two projects still collides: {e}")
    finally:
        globals()["ZoteroClient"] = real

    # --- placement round trip -------------------------------------------
    # simulate: a card was materialised as a note, then dragged in Zotero into
    # _KJ/Oversight. A re-import must read that back as the grouping and must
    # NOT create a second card from the same note.
    conn3 = open_db(path)
    pid3 = get_project(conn3, "selftest")["id"]
    target = conn3.execute(
        "SELECT * FROM card WHERE project_id = ? AND origin_key = ?",
        (pid3, "annotation:ANN1:quote")).fetchone()
    conn3.execute("UPDATE card SET zotero_note_key = 'KJNOTE1' WHERE id = ?",
                  (target["id"],))
    other = conn3.execute(
        "SELECT * FROM card WHERE project_id = ? AND origin_key = ?",
        (pid3, "annotation:ANN4:quote")).fetchone()
    conn3.execute("UPDATE card SET zotero_note_key = 'KJNOTE2' WHERE id = ?",
                  (other["id"],))
    conn3.commit()
    conn3.close()

    FIXTURE["collections"] += [
        {"data": {"key": "KJ", "name": "_KJ", "parentCollection": "ROOT"}},
        {"data": {"key": "THEME1", "name": "Oversight is organisational",
                  "parentCollection": "KJ"}},
    ]
    FIXTURE["collections"].append(
        {"data": {"key": "INBOX", "name": "Inbox", "parentCollection": "KJ"}})
    kjnote1 = {
        "data": {"key": "KJNOTE1", "itemType": "note",
                 "note": "<h2>KJ-0001 quote</h2><p>kj:card=KJ-0001</p>",
                 "tags": [{"tag": KJ_TAG}, {"tag": "kj-kind:quote"}],
                 # dragged into a theme but still also in Inbox: two sightings,
                 # one card
                 "collections": ["THEME1", "INBOX"]}
    }
    kjnote2 = {
        "data": {"key": "KJNOTE2", "itemType": "note",
                 "note": "<h2>KJ-0003 quote</h2><p>kj:card=KJ-0003</p>",
                 "tags": [{"tag": KJ_TAG}, {"tag": "kj-kind:quote"}],
                 "collections": ["INBOX"]}          # never sorted
    }
    FIXTURE["items"]["KJ"] = []
    FIXTURE["items"]["THEME1"] = [kjnote1]
    FIXTURE["items"]["INBOX"] = [kjnote1, kjnote2]

    globals()["ZoteroClient"] = FakeClient
    try:
        stats_rt = run_import("selftest", "ROOT", use_google_books=False,
                              db_path=path)
    finally:
        globals()["ZoteroClient"] = real

    conn4 = open_db(path)
    pid4 = get_project(conn4, "selftest")["id"]
    rows4 = fetch_cards(conn4, pid4)
    check(len(rows4) == len(rows),
          f"KJ note was re-imported as a new card: {len(rows)} -> {len(rows4)}")
    check(stats_rt.placements_read == 2,
          f"placements miscounted: expected 2 cards, got "
          f"{stats_rt.placements_read} (a note in two collections must count once)")
    check(stats_rt.still_in_inbox == 1,
          f"unsorted card not reported (got {stats_rt.still_in_inbox})")
    moved = conn4.execute("SELECT kj_path FROM card WHERE id = ?",
                          (target["id"],)).fetchone()
    check(moved["kj_path"] == "Agentic Governance/_KJ/Oversight is organisational",
          f"kj_path wrong: {moved['kj_path']}")
    conn4.close()

    for m in ("server_info", "collections", "collection_items_top", "children",
              "items", "file_url", "authorize_write", "create_items",
              "create_collections"):
        check(callable(getattr(real, m, None)),
              f"ZoteroClient lost method {m}() -- class body was broken")

    # --- group label round trip -----------------------------------------
    import tempfile as _tf
    wpath = os.path.join(_tf.mkdtemp(), "groups.md")

    class _A:
        pass
    a = _A(); a.db = path; a.project = "selftest"
    a.export = wpath; a.import_file = None
    cmd_groups(a)
    body = open(wpath, encoding="utf-8").read()
    check("## Agentic Governance/_KJ/Oversight is organisational" in body,
          "worksheet missing the group the card was filed into")
    check(body.count("LABEL:") >= 1, "worksheet has no LABEL slot")

    marker = "## Agentic Governance/_KJ/Oversight is organisational\nLABEL: "
    check(marker in body, "worksheet layout changed; test cannot target a group")
    body = body.replace(
        marker, marker + "Oversight is an organisational property", 1)
    open(wpath, "w", encoding="utf-8").write(body)
    a.export = None; a.import_file = wpath
    cmd_groups(a)

    conn5 = open_db(path)
    pid5 = get_project(conn5, "selftest")["id"]
    labels = conn5.execute(
        "SELECT * FROM card WHERE project_id = ? AND origin = 'group_label'",
        (pid5,)).fetchall()
    check(len(labels) == 1, f"expected 1 label card, got {len(labels)}")
    if labels:
        check(labels[0]["kind"] == "idea",
              "a group label is an idea card")
        check(labels[0]["kj_path"] == "Agentic Governance/_KJ/Oversight is organisational",
              f"label filed wrong: {labels[0]['kj_path']}")
        check(labels[0]["zotero_note_key"] is None,
              "a fresh label must still await materialising")
    cmd_groups(a)   # importing twice must not duplicate
    n_labels = conn5.execute(
        "SELECT COUNT(*) c FROM card WHERE project_id = ? AND origin = 'group_label'",
        (pid5,)).fetchone()["c"]
    check(n_labels == 1, f"re-import duplicated label cards: {n_labels}")
    label_row = conn5.execute(
        CARD_QUERY + " AND c.origin = 'group_label' LIMIT 1", (pid5,)).fetchone()
    quote_row = conn5.execute(
        CARD_QUERY + " AND c.kind = 'quote' LIMIT 1", (pid5,)).fetchone()
    ck = {"Agentic Governance/_KJ/Oversight is organisational": "THEME1"}
    check(target_collection(label_row, ck, "INBOX") == "THEME1",
          "a group label must be filed with the group it names, not in Inbox")
    check(target_collection(quote_row, ck, "INBOX") == "INBOX",
          "a quote card must land in Inbox for the researcher to sort")
    conn5.close()

    # note rendering must escape user text rather than emit raw HTML
    target_full = next(r for r in rows4 if r["id"] == target["id"])
    rendered = render_note(target_full, "selftest", "KJ-0002")
    check("<script" not in rendered, "note rendering does not escape input")
    check("kj:card=" in rendered and KJ_TAG not in rendered.replace("kj-card", ""),
          "note marker block missing")

    print(stats.render())
    if failures:
        print("\nFAILED:")
        for f in failures:
            print(f"  - {f}")
        raise SystemExit(1)
    print("\nselftest OK")


# --------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(prog="zkj_v0", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=DB_PATH)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check").set_defaults(func=cmd_check)
    sub.add_parser("collections").set_defaults(func=cmd_collections)

    imp = sub.add_parser("import")
    imp.add_argument("--collection", required=True, help="root collection key")
    imp.add_argument("--project", required=True)
    imp.add_argument("--google-books", action="store_true",
                     help="look up print page counts to annotate EPUB chapter "
                          "locators with an ESTIMATED page (never authoritative)")
    imp.set_defaults(func=cmd_import)

    cards = sub.add_parser("cards")
    cards.add_argument("--project", required=True)
    cards.add_argument("--kind", choices=["quote", "idea", "image"])
    cards.add_argument("--export", help="cards.md or cards.csv")
    cards.add_argument("--limit", type=int, default=20)
    cards.set_defaults(func=cmd_cards)

    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("--project", required=True)
    cmp_.add_argument("--k", type=int, help="clusters (default: your folder count)")
    cmp_.add_argument("--show", type=int, default=15)
    cmp_.set_defaults(func=cmd_compare)

    auth = sub.add_parser("authorize")
    auth.add_argument("--force", action="store_true")
    auth.set_defaults(func=cmd_authorize)

    mat = sub.add_parser("materialize")
    mat.add_argument("--project", required=True)
    mat.add_argument("--kind", nargs="*", choices=["quote", "idea"],
                     help="default: both")
    mat.add_argument("--limit", type=int, help="materialise only the first N")
    mat.add_argument("--dry-run", action="store_true",
                     help="show the notes that would be created, write nothing")
    mat.set_defaults(func=cmd_materialize)

    grp = sub.add_parser("groups")
    grp.add_argument("--project", required=True)
    grp.add_argument("--export", help="worksheet path (default groups.md)")
    grp.add_argument("--import", dest="import_file",
                     help="read filled-in labels back from this worksheet")
    grp.set_defaults(func=cmd_groups)

    st = sub.add_parser("status")
    st.set_defaults(func=cmd_status)

    diag = sub.add_parser("diagnose")
    diag.add_argument("--collection", required=True)
    diag.add_argument("-n", type=int, default=8,
                      help="how many attachments to inspect in detail")
    diag.set_defaults(func=cmd_diagnose)

    sub.add_parser("selftest").set_defaults(func=cmd_selftest)

    args = p.parse_args()
    try:
        args.func(args)
    except ZoteroError as e:
        raise SystemExit(f"Zotero error: {e}")


if __name__ == "__main__":
    main()
