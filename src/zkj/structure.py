"""Your outline against your evidence.

The one comparison no other tool can make: the researcher's own folders — or
the groups they dragged cards into — set against what the card texts actually
cluster into. The output that matters is the misfit list: cards whose text
sits with a different chapter than the one they were filed under.

A misfit is **a card worth re-reading**, never a proposed reorganisation. This
is bag-of-character-n-grams; it sees vocabulary, not argument. A card can be
lexically identical to chapter 3 and belong in chapter 5.

These numbers are also the baseline for any future AI clustering. If
embeddings and a model cannot beat TF-IDF and Ward, the model is not earning
its cost.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score

from .cards import CARD_SELECT, citation_of
from .similarity import vectorise

MIN_CARDS = 10
MIN_TEXT = 20
DEGENERATE_SHARE = 0.7
NEAREST_PER_CLUSTER = 3


class NotEnoughToCompare(RuntimeError):
    """Said plainly rather than returned as a meaningless zero."""


@dataclass
class StructureResult:
    basis: str
    basis_label: str
    cards_used: int
    groups: list[str]
    k: int
    ari: float
    nmi: float
    contingency: list[list[int]] = field(default_factory=list)
    clusters: list[dict[str, Any]] = field(default_factory=list)
    misfits: list[dict[str, Any]] = field(default_factory=list)
    degenerate: bool = False
    warning: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "basis": self.basis,
            "basis_label": self.basis_label,
            "cards_used": self.cards_used,
            "groups": self.groups,
            "k": self.k,
            "ari": self.ari,
            "nmi": self.nmi,
            "contingency": self.contingency,
            "clusters": self.clusters,
            "misfits": self.misfits,
            "degenerate": self.degenerate,
            "warning": self.warning,
        }


def _rows(conn: sqlite3.Connection, project_id: str, column: str) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            CARD_SELECT
            + f" WHERE c.project_id = ? AND c.kind != 'image' AND c.status = 'active' "
            f"AND c.{column} IS NOT NULL AND LENGTH(c.text) > ? "
            f"ORDER BY c.{column}, c.human_id",
            (project_id, MIN_TEXT),
        )
    ]


def choose_basis(conn: sqlite3.Connection, project_id: str) -> tuple[str, str]:
    """Prefer the groups the researcher made; fall back to their folders."""
    grouped = _rows(conn, project_id, "kj_path")
    if len(grouped) >= MIN_CARDS and len({r["kj_path"] for r in grouped}) >= 2:
        return "kj_path", "the groups you made under _KJ"
    return "prior_path", "the folders your sources sit in"


def compare(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    basis: str | None = None,
    k: int | None = None,
    max_misfits: int = 50,
) -> StructureResult:
    column, label = (basis, {"kj_path": "the groups you made under _KJ"}.get(
        basis, "the folders your sources sit in"
    )) if basis else choose_basis(conn, project_id)

    rows = _rows(conn, project_id, column)
    if len(rows) < MIN_CARDS:
        raise NotEnoughToCompare(
            f"Only {len(rows)} cards carry both a text and a place in your "
            f"structure. Below about {MIN_CARDS} the numbers say nothing."
        )
    groups = sorted({r[column] for r in rows})
    if len(groups) < 2:
        raise NotEnoughToCompare(
            "Every card sits in one place, so there is nothing to compare it "
            "against. Make at least two groups first."
        )

    X = vectorise([r["text"] for r in rows], min_df=2)
    clusters_wanted = k or len(groups)
    clusters_wanted = max(2, min(clusters_wanted, len(rows) - 1))

    # Ward on L2-normalised vectors. Average linkage was tried and chains into
    # one giant cluster on sparse text; Ward scored materially better on the
    # same data.
    model = AgglomerativeClustering(n_clusters=clusters_wanted, linkage="ward")
    emergent = model.fit_predict(X)

    index_of = {g: i for i, g in enumerate(groups)}
    mine = np.array([index_of[r[column]] for r in rows])

    contingency = [[0] * clusters_wanted for _ in groups]
    for own, cluster in zip(mine, emergent, strict=True):
        contingency[own][cluster] += 1

    home = {
        c: max(range(len(groups)), key=lambda g: contingency[g][c])
        for c in range(clusters_wanted)
    }

    clusters: list[dict[str, Any]] = []
    for c in range(clusters_wanted):
        members = np.where(emergent == c)[0]
        centroid = X[members].mean(axis=0)
        nearest = members[np.argsort(-(X[members] @ centroid))][:NEAREST_PER_CLUSTER]
        clusters.append(
            {
                "index": int(c),
                "size": int(len(members)),
                "mostly": groups[home[c]],
                "nearest": [
                    {
                        "human_id": rows[i]["human_id"],
                        "kind": rows[i]["kind"],
                        "text": rows[i]["text"],
                        "citation": citation_of(rows[i]),
                    }
                    for i in nearest
                ],
            }
        )

    misfits = [
        {
            "id": row["id"],
            "human_id": row["human_id"],
            "kind": row["kind"],
            "text": row["text"],
            "citation": citation_of(row),
            "filed_in": row[column],
            "clusters_with": groups[home[cluster]],
        }
        for row, cluster in zip(rows, emergent, strict=True)
        if home[cluster] != index_of[row[column]]
    ]

    sizes = [c["size"] for c in clusters]
    degenerate = max(sizes) > DEGENERATE_SHARE * len(rows)

    return StructureResult(
        basis=column,
        basis_label=label,
        cards_used=len(rows),
        groups=groups,
        k=clusters_wanted,
        ari=float(adjusted_rand_score(mine, emergent)),
        nmi=float(normalized_mutual_info_score(mine, emergent)),
        contingency=contingency,
        clusters=clusters,
        misfits=misfits[:max_misfits],
        degenerate=degenerate,
        warning=(
            "One cluster holds most of the cards, so these texts are too "
            "uniform in vocabulary for this method. Treat the scores as "
            "unreliable."
            if degenerate
            else None
        ),
    )
