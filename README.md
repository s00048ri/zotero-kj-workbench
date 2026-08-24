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
.venv/bin/python -m zkj          # opens http://127.0.0.1:8420/
```

## Tests

```
.venv/bin/python -m pytest
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
```

## Milestones

| | | |
|---|---|---|
| M1 | Zotero adapter and status page | **done** |
| M2 | Import, cards, locators, Cards screen | next |
| M3 | Notes into Zotero | |
| M4 | Placement read-back and Groups | |
| M5 | Add my note | |
| M6 | Structure comparison | |
| M7 | Compose and prompt export | |
