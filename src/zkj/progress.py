"""Where the researcher is in the loop, and what the next move is.

The loop only works if each step is visible from inside the app:

    read the collection → create notes → sort them in Zotero →
    read the grouping back → write a label per group → compare

The first version of this interface had every one of those actions available
somewhere, and a researcher still granted write permission and then went
straight to re-reading — because nothing on screen said that notes had to
exist before there was anything to sort. Knowing the steps is not the same as
seeing which one you are on.

So the state of every step is computed here, from data, in one place.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .cards import summary
from .compose import chosen_question, list_sections
from .groups import group_summary

STEPS = ("read", "notes", "sort", "label", "compare", "question", "write")


@dataclass
class Step:
    key: str
    done: bool
    detail: str
    count: int = 0
    # An optional step is never what the loop points at. Choosing a question
    # and naming sections are ways of taking control of decisions the model
    # would otherwise make — worth doing, never required first.
    optional: bool = False


@dataclass
class Progress:
    current: str
    steps: list[Step] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    kj_root_key: str | None = None
    kj_inbox_key: str | None = None
    writes_available: bool = True
    last_import_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "current": self.current,
            "steps": [
                {
                    "key": s.key,
                    "done": s.done,
                    "detail": s.detail,
                    "count": s.count,
                    "optional": s.optional,
                }
                for s in self.steps
            ],
            "counts": self.counts,
            "kj_root_key": self.kj_root_key,
            "kj_inbox_key": self.kj_inbox_key,
            "writes_available": self.writes_available,
            "last_import_at": self.last_import_at,
        }


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def progress(
    conn: sqlite3.Connection,
    project: dict[str, Any],
    *,
    writes_available: bool = True,
) -> Progress:
    project_id = project["id"]
    cards = summary(conn, project_id)
    groups = group_summary(conn, project_id)

    pending = conn.execute(
        "SELECT COUNT(*) FROM card WHERE project_id = ? AND zotero_note_key IS NULL "
        "AND status = 'active' AND kind IN ('quote', 'idea')",
        (project_id,),
    ).fetchone()[0]
    in_zotero = conn.execute(
        "SELECT COUNT(*) FROM card WHERE project_id = ? AND materialized_at IS NOT NULL",
        (project_id,),
    ).fetchone()[0]
    # A card whose note has been seen by an import knows where it sits, even
    # if that place is only the Inbox. One that has never been seen is the
    # difference between "not sorted yet" and "not read back yet".
    read_back = conn.execute(
        "SELECT COUNT(*) FROM card WHERE project_id = ? AND materialized_at IS NOT NULL "
        "AND kj_collection_keys_json IS NOT NULL",
        (project_id,),
    ).fetchone()[0]

    counts = {
        **cards,
        "pending_notes": pending,
        "in_zotero": in_zotero,
        "read_back": read_back,
        "in_inbox": in_zotero - groups["cards_grouped"] if in_zotero else 0,
        **{f"group_{k}": v for k, v in groups.items()},
    }

    steps: list[Step] = []

    steps.append(
        Step(
            key="read",
            done=cards["total"] > 0,
            count=cards["total"],
            detail=(
                f"{_plural(cards['quotes'], 'passage')} you highlighted"
                + (
                    f", and {_plural(cards['ideas'], 'card')} in your own words"
                    if cards["ideas"]
                    else ""
                )
                if cards["total"]
                else "Nothing read yet."
            ),
        )
    )

    steps.append(
        Step(
            key="notes",
            done=in_zotero > 0 and pending == 0,
            count=pending,
            detail=(
                "Every card is a note in Zotero."
                if in_zotero and not pending
                else f"{_plural(pending, 'card')} cannot be filed in Zotero yet. "
                "A highlight belongs to a PDF, not to a collection — as a note "
                "it can go anywhere."
                if pending
                else "Nothing to write."
            ),
        )
    )

    steps.append(
        Step(
            key="sort",
            done=groups["cards_grouped"] > 0,
            count=counts["in_inbox"],
            detail=(
                f"{_plural(groups['groups'], 'group')} recovered, "
                f"{_plural(counts['in_inbox'], 'card')} still in Inbox"
                if groups["cards_grouped"]
                else f"{_plural(in_zotero, 'note')} waiting in _KJ/Inbox for you "
                "to sort in Zotero"
                if in_zotero and read_back
                else f"{_plural(in_zotero, 'note')} written. Sort them in Zotero, "
                "then read the grouping back here."
                if in_zotero
                else "Nothing to sort yet."
            ),
        )
    )

    steps.append(
        Step(
            key="label",
            done=groups["groups"] > 0 and groups["labelled"] == groups["groups"],
            count=groups["groups"] - groups["labelled"],
            detail=(
                f"{groups['labelled']} of {_plural(groups['groups'], 'group')} "
                "have a proposition written"
                if groups["groups"]
                else "One sentence per group, once the groups exist."
            ),
        )
    )

    steps.append(
        Step(
            key="compare",
            done=False,
            count=cards["total"],
            detail=(
                "Your own structure against what the card texts cluster into."
            ),
        )
    )

    question = chosen_question(conn, project_id)
    candidates = conn.execute(
        "SELECT COUNT(*) FROM research_question WHERE project_id = ?", (project_id,)
    ).fetchone()[0]
    steps.append(
        Step(
            key="question",
            optional=True,
            done=question is not None,
            count=candidates,
            detail=(
                question["text"]
                if question
                else f"{_plural(candidates, 'candidate')} written, none chosen yet"
                if candidates
                else "Optional. Choose one and every prompt will keep it; leave "
                "it and the model proposes one out of your groups."
            ),
        )
    )

    sections = list_sections(conn, project_id)
    drafted = {
        r["section_id"]
        for r in conn.execute(
            "SELECT DISTINCT section_id FROM draft WHERE project_id = ? "
            "AND section_id IS NOT NULL",
            (project_id,),
        )
    }
    whole_paper = conn.execute(
        "SELECT COUNT(*) FROM draft WHERE project_id = ? AND section_id IS NULL",
        (project_id,),
    ).fetchone()[0]
    with_evidence = [s for s in sections if s["evidence_count"]]
    steps.append(
        Step(
            key="write",
            done=bool(whole_paper)
            or (bool(with_evidence) and all(s["id"] in drafted for s in with_evidence)),
            count=len(with_evidence) - len([s for s in with_evidence if s["id"] in drafted]),
            detail=(
                f"{whole_paper} drafts of the whole paper"
                + (
                    f", and {len(drafted)} of {len(sections)} sections drafted"
                    if sections
                    else ""
                )
                if whole_paper
                else f"{len(sections)} sections, {len(with_evidence)} with evidence "
                f"assigned, {len(drafted)} drafted"
                if sections
                else "Build one prompt from your groups and paste back the paper "
                "it writes — or name the sections yourself first."
            ),
        )
    )

    counts["sections"] = len(sections)
    counts["sections_with_evidence"] = len(with_evidence)
    counts["sections_drafted"] = len(drafted & {s["id"] for s in sections})
    counts["questions"] = candidates
    counts["whole_paper_drafts"] = whole_paper

    current = "read"
    if not cards["total"]:
        current = "read"
    elif pending and not in_zotero:
        current = "notes"
    elif in_zotero and not groups["cards_grouped"]:
        current = "sort"
    elif groups["groups"] and groups["labelled"] < groups["groups"]:
        current = "label"
    elif groups["groups"] and not steps[-1].done:
        # Writing comes next once there are groups. Choosing a question first
        # is a way of taking control of it, not a gate to pass.
        current = "write"
    elif groups["groups"]:
        current = "compare"
    elif pending:
        current = "notes"

    return Progress(
        current=current,
        steps=steps,
        counts=counts,
        kj_root_key=project.get("kj_root_key"),
        kj_inbox_key=project.get("kj_inbox_key"),
        writes_available=writes_available,
        last_import_at=project.get("last_import_at"),
    )
