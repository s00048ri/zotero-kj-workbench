"""Groups, labels, and the outline-versus-evidence comparison."""

from __future__ import annotations

import pytest

from tests.conftest import FakeZotero
from zkj.groups import as_dict, group_summary, list_groups, save_label, ungrouped_count
from zkj.importer import run_import
from zkj.similarity import least_alike
from zkj.store import connect, now_iso
from zkj.structure import NotEnoughToCompare, compare


@pytest.fixture
def project(tmp_path):
    fake = FakeZotero()
    conn = connect(tmp_path / "g.sqlite3")
    with fake.client() as client:
        project_id, _ = run_import(conn, client, "p", "ROOT")
    yield conn, project_id
    conn.close()


def quote_ids(conn) -> list[str]:
    return [r["human_id"] for r in conn.execute(
        "SELECT human_id FROM card WHERE kind = 'quote' ORDER BY human_id")]


def file_cards(conn, project_id, path: str, human_ids: list[str]) -> None:
    """Stand in for dragging notes into a subcollection in Zotero."""
    for human_id in human_ids:
        conn.execute(
            "UPDATE card SET kj_path = ?, zotero_note_key = ?, materialized_at = ? "
            "WHERE project_id = ? AND human_id = ?",
            (path, f"NOTE-{human_id}", now_iso(), project_id, human_id),
        )


# -- groups ---------------------------------------------------------------


def test_a_group_is_a_collection_holding_cards(project):
    conn, pid = project
    file_cards(conn, pid, "P/_KJ/Oversight", quote_ids(conn)[:2])
    groups = list_groups(conn, pid)
    assert [g.path for g in groups] == ["P/_KJ/Oversight"]
    assert groups[0].name == "Oversight"
    assert groups[0].size == 2
    assert groups[0].label is None


def test_a_lone_idea_card_is_not_a_group_worth_labelling(project):
    """It is already its own statement; asking for a label about it is noise."""
    conn, pid = project
    idea = conn.execute(
        "SELECT human_id FROM card WHERE origin = 'standalone_note'"
    ).fetchone()["human_id"]
    file_cards(conn, pid, "P/_KJ/Alone", [idea])
    assert list_groups(conn, pid) == []


def test_a_lone_quote_is_a_group(project):
    conn, pid = project
    quote = conn.execute(
        "SELECT human_id FROM card WHERE kind = 'quote' LIMIT 1"
    ).fetchone()["human_id"]
    file_cards(conn, pid, "P/_KJ/One passage", [quote])
    assert [g.size for g in list_groups(conn, pid)] == [1]


def test_cards_left_in_the_inbox_are_counted_as_ungrouped(project):
    conn, pid = project
    quotes = [r["human_id"] for r in conn.execute(
        "SELECT human_id FROM card WHERE kind = 'quote' ORDER BY human_id")]
    conn.execute(
        "UPDATE card SET zotero_note_key = 'N', materialized_at = ? "
        "WHERE kind = 'quote'", (now_iso(),)
    )
    file_cards(conn, pid, "P/_KJ/Oversight", quotes[:1])
    assert ungrouped_count(conn, pid) == 2
    assert group_summary(conn, pid)["ungrouped"] == 2


def test_the_least_alike_pair_is_offered_as_a_prompt(project):
    conn, pid = project
    file_cards(conn, pid, "P/_KJ/Mixed", quote_ids(conn)[:3])
    group = list_groups(conn, pid)[0]
    assert group.least_alike is not None
    assert len(set(group.least_alike)) == 2


def test_least_alike_needs_three_texts_to_mean_anything():
    assert least_alike(["a passage", "another passage"]) is None
    pair = least_alike(
        ["regulation and capacity", "regulation and capacity again", "まったく別の話題"]
    )
    assert pair is not None
    assert pair.similarity < 0.5


# -- labels ---------------------------------------------------------------


def test_writing_a_label_makes_an_idea_card_filed_with_its_group(project):
    conn, pid = project
    file_cards(conn, pid, "P/_KJ/Oversight", quote_ids(conn)[:2])
    label = save_label(conn, pid, "P/_KJ/Oversight", "Oversight is organisational.")
    assert label["kind"] == "idea"
    assert label["origin"] == "group_label"
    assert label["kj_path"] == "P/_KJ/Oversight"
    assert label["zotero_note_key"] is None  # not yet pushed


def test_re_saving_a_label_updates_the_same_card(project):
    conn, pid = project
    file_cards(conn, pid, "P/_KJ/Oversight", quote_ids(conn)[:2])
    first = save_label(conn, pid, "P/_KJ/Oversight", "First try.")
    second = save_label(conn, pid, "P/_KJ/Oversight", "Better sentence.")
    assert first["id"] == second["id"]
    assert second["text"] == "Better sentence."
    assert conn.execute(
        "SELECT COUNT(*) FROM card WHERE origin = 'group_label'"
    ).fetchone()[0] == 1


def test_a_label_can_carry_a_longer_note_under_its_sentence(project):
    conn, pid = project
    file_cards(conn, pid, "P/_KJ/Oversight", quote_ids(conn)[:2])
    label = save_label(conn, pid, "P/_KJ/Oversight", "A proposition.", "Why I think so.")
    assert label["text"] == "A proposition.\n\nWhy I think so."


def test_an_empty_label_is_refused(project):
    conn, pid = project
    file_cards(conn, pid, "P/_KJ/Oversight", quote_ids(conn)[:2])
    with pytest.raises(ValueError):
        save_label(conn, pid, "P/_KJ/Oversight", "   ")


def test_a_label_is_not_a_member_of_its_own_group(project):
    conn, pid = project
    file_cards(conn, pid, "P/_KJ/Oversight", quote_ids(conn)[:2])
    save_label(conn, pid, "P/_KJ/Oversight", "A proposition.")
    group = list_groups(conn, pid)[0]
    assert group.size == 2
    assert group.label["text"] == "A proposition."
    assert as_dict(group)["label"]["in_zotero"] is False


# -- structure ------------------------------------------------------------


def bulk_cards(conn, project_id, path: str, texts: list[str], start: int) -> None:
    for offset, text in enumerate(texts):
        conn.execute(
            "INSERT INTO card (id, project_id, human_id, origin_key, kind, origin, "
            "text, prior_path, kj_path, content_hash, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'quote', 'manual', ?, ?, ?, 'h', ?, ?)",
            (
                f"id{start + offset}",
                project_id,
                f"KJ-9{start + offset:03d}",
                f"manual:{start + offset}",
                text,
                path,
                path,
                now_iso(),
                now_iso(),
            ),
        )


OVERSIGHT = [
    "Human oversight of autonomous agents fails across organisational boundaries.",
    "Oversight is treated as an individual capability rather than an organisational one.",
    "Auditability is not oversight, and conflating them weakens both.",
    "Supervisory attention degrades as the number of agents under oversight grows.",
    "Oversight regimes assume a human who is watching, and nobody is watching.",
    "The oversight literature keeps describing an individual watching a screen.",
]
CAPACITY = [
    "Regulatory capacity lags far behind the pace of deployment.",
    "Fiscal capacity determines which regulations can be enforced at all.",
    "State capacity is a budget question before it is a legal question.",
    "Agencies lack the staff to enforce the rules they already have.",
    "Enforcement capacity, not rule-making, is the binding constraint.",
    "Budget cycles set the tempo of regulatory enforcement.",
]


def test_the_comparison_reports_agreement_and_the_cards_to_re_read(project):
    conn, pid = project
    conn.execute("DELETE FROM card")
    bulk_cards(conn, pid, "P/Oversight", OVERSIGHT, 0)
    bulk_cards(conn, pid, "P/Capacity", CAPACITY, 100)
    # one card filed in the wrong chapter on purpose
    bulk_cards(conn, pid, "P/Capacity", [OVERSIGHT[0] + " Oversight again."], 200)

    result = compare(conn, pid, basis="prior_path")
    assert result.cards_used == 13
    assert result.groups == ["P/Capacity", "P/Oversight"]
    assert result.k == 2
    assert -1.0 <= result.ari <= 1.0
    assert 0.0 <= result.nmi <= 1.0
    assert sum(sum(row) for row in result.contingency) == 13
    assert result.misfits
    assert result.misfits[0]["filed_in"] != result.misfits[0]["clusters_with"]


def test_each_cluster_is_interpretable_without_asking_a_model(project):
    conn, pid = project
    conn.execute("DELETE FROM card")
    bulk_cards(conn, pid, "P/Oversight", OVERSIGHT, 0)
    bulk_cards(conn, pid, "P/Capacity", CAPACITY, 100)
    result = compare(conn, pid, basis="prior_path")
    assert all(1 <= len(c["nearest"]) <= 3 for c in result.clusters)
    assert all(c["mostly"] in result.groups for c in result.clusters)


def test_a_degenerate_result_is_flagged_rather_than_reported_as_a_score(project):
    conn, pid = project
    conn.execute("DELETE FROM card")
    same = ["Regulatory capacity lags behind deployment everywhere." for _ in range(11)]
    bulk_cards(conn, pid, "P/A", same, 0)
    bulk_cards(conn, pid, "P/B", ["Regulatory capacity lags behind deployment too."], 100)
    result = compare(conn, pid, basis="prior_path")
    assert result.degenerate
    assert "unreliable" in result.warning


def test_too_few_cards_is_said_plainly(project):
    conn, pid = project
    with pytest.raises(NotEnoughToCompare, match="cards"):
        compare(conn, pid, basis="prior_path")


def test_one_group_is_nothing_to_compare(project):
    conn, pid = project
    conn.execute("DELETE FROM card")
    bulk_cards(conn, pid, "P/Only", OVERSIGHT + CAPACITY, 0)
    with pytest.raises(NotEnoughToCompare, match="one place"):
        compare(conn, pid, basis="prior_path")


def test_the_comparison_prefers_the_groups_you_made(project):
    conn, pid = project
    conn.execute("DELETE FROM card")
    bulk_cards(conn, pid, "P/_KJ/Oversight", OVERSIGHT, 0)
    bulk_cards(conn, pid, "P/_KJ/Capacity", CAPACITY, 100)
    result = compare(conn, pid)
    assert result.basis == "kj_path"
    assert "groups you made" in result.basis_label


def test_a_group_label_is_not_clustered_with_its_own_group(project):
    """It is a sentence about that group, so counting it would be circular."""
    conn, pid = project
    conn.execute("DELETE FROM card")
    bulk_cards(conn, pid, "P/_KJ/Oversight", OVERSIGHT, 0)
    bulk_cards(conn, pid, "P/_KJ/Capacity", CAPACITY, 100)
    before = compare(conn, pid).cards_used

    save_label(conn, pid, "P/_KJ/Oversight", "Oversight is organisational.")
    save_label(conn, pid, "P/_KJ/Capacity", "Capacity is fiscal.")
    assert compare(conn, pid).cards_used == before
