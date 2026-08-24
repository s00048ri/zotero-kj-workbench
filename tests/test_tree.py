"""The collection tree is the researcher's outline, so paths are data."""

from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zkj.zotero import CollectionTree, ZoteroError


@pytest.fixture
def tree() -> CollectionTree:
    return CollectionTree.from_payloads(load_fixture()["collections"])


def test_paths_and_depths(tree):
    assert tree.get("ROOT").path == "Agentic Governance"
    assert tree.get("CH02").path == "Agentic Governance/02 Oversight"
    assert tree.get("CH03A").path == "Agentic Governance/03 Capacity/03a Fiscal"
    assert tree.get("CH03A").depth == 2


def test_top_level_parent_is_false_not_null(tree):
    assert tree.get("ROOT").parent_key is None
    assert tree.get("ROOT") in tree.roots


def test_collection_with_a_missing_parent_is_kept_as_a_root(tree):
    """A partial response must not make collections disappear."""
    orphan = tree.get("ORPHAN")
    assert orphan in tree.roots
    assert orphan.path == "Shared elsewhere"


def test_subtree_is_the_root_and_its_descendants(tree):
    keys = set(tree.subtree_keys("ROOT"))
    assert keys == {"ROOT", "CH02", "CH03", "CH03A"}
    assert "OTHER" not in keys
    assert tree.subtree_keys("CH03") == ["CH03", "CH03A"]


def test_children_are_sorted_and_lookups_work(tree):
    assert [c.name for c in tree.get("ROOT").children] == ["02 Oversight", "03 Capacity"]
    assert tree.by_path("Agentic Governance/03 Capacity").key == "CH03"
    assert tree.child_named("ROOT", "02 Oversight").key == "CH02"
    assert tree.child_named("ROOT", "_KJ") is None


def test_unknown_collection_is_an_error_with_a_readable_message(tree):
    with pytest.raises(ZoteroError, match="NOPE"):
        tree.get("NOPE")


def test_a_cycle_does_not_hang():
    cyclic = [
        {"data": {"key": "A", "name": "A", "parentCollection": "B"}},
        {"data": {"key": "B", "name": "B", "parentCollection": "A"}},
        {"data": {"key": "C", "name": "C", "parentCollection": False}},
    ]
    tree = CollectionTree.from_payloads(cyclic)
    assert tree.get("C").path == "C"
