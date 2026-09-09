# Running this on your own machine

Everything left in Phase 0 needs a machine this repository's development
container does not have: one with Zotero installed, a browser signed in to
Google, and your own library. Nothing about that can be worked around —
the checks exist precisely because they cannot be answered from anywhere else.

## Where to put it

Wherever you already have it. On the Windows machine that is
`C:\Users\<you>\projects\zotero-kj-workbench`, and there is no reason to move
it. Three things matter more than the location:

* **not inside a synced folder** — OneDrive, Dropbox and iCloud Drive all
  fight with `.git` and with a virtualenv, and the failures are baffling.
  `C:\Users\<you>\projects\` is outside OneDrive by default; a path under
  `Documents` or `Desktop` may not be, because Windows can redirect those into
  OneDrive without saying so;
* **a path you can type** — the virtualenv bakes absolute paths into scripts,
  so moving the folder afterwards means rebuilding `.venv`;
* **one clone per machine.** The Mac and the Windows box each keep their own
  clone, their own `.venv`, and their own database. Git carries the code
  between them; nothing else is shared.

## Bringing it up to date

```powershell
cd C:\Users\<you>\projects\zotero-kj-workbench
git checkout claude/zotero-notebooklm-plugin-9665kl
git pull
```

If `.venv` already exists from an earlier session, it is still good — the
dependencies have not changed. If not, see the Running section of the
[README](../README.md).

## Phase 0, on your machine

Start Zotero and leave it running, with Settings → Advanced → "Allow other
applications on this computer to communicate with Zotero" on.

```powershell
.\docs\phase0\verify.ps1
```

or, on macOS and Linux, or in Git Bash:

```bash
bash docs/phase0/verify.sh
```

Both read only: they ask Zotero questions and write the answers to
`docs/phase0/v1-output.txt`. Nothing is written into Zotero, and nothing
leaves the machine. Paste the file into [findings.md](findings.md) under V1,
or hand it over as it is.

The script needs `curl`. Windows 10 and 11 ship it as
`C:\Windows\System32\curl.exe`, which is what the PowerShell version calls —
by that name, deliberately, because in PowerShell `curl` alone is an alias for
`Invoke-WebRequest`, which follows redirects and throws on anything that is
not a 2xx. Seeing the raw 302 is the whole point of V1.

## What the spec says about platforms

[The spec](spec-nblm.md) names macOS as the target and puts Windows under
non-goals — "動けばよいが検証しない". That applies to **Tracks A and B**, the
Zotero plugin and the Chrome extension, whose packaging and paths differ per
platform.

It does not apply to Phase 0 or to the bridge. V1 asks what Zotero's local API
does, and the answer is worth having from either machine — arguably from both,
since a difference between them would itself be a finding. The bridge is
Python and FastAPI with no platform-specific parts in the design.

So: run Phase 0 wherever is convenient. Decide later, on evidence, whether the
plugin and the extension are built for one machine or two.
