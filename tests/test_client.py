"""The adapter's contract with the local API."""

from __future__ import annotations

import pytest

from zkj.zotero import ZoteroClient, ZoteroForbidden, ZoteroUnreachable
from tests.conftest import FakeZotero


def test_capability_detected_from_headers(client, fake_zotero):
    info = client.server_info()
    assert info.reachable
    assert info.api_version == "3"
    assert info.server_id == "TESTSERVER01"
    assert info.zotero_version == "10.0"
    assert info.writes_available is True


def test_no_server_id_means_no_writes():
    """Zotero 9 answers reads happily and must not be treated as broken."""
    fake = FakeZotero(headers={"Zotero-API-Version": "3"})
    with fake.client() as c:
        info = c.server_info()
    assert info.reachable
    assert info.server_id is None
    assert info.writes_available is False


def test_server_info_is_cached_until_refreshed(client, fake_zotero):
    client.server_info()
    client.server_info()
    assert fake_zotero.count("/api/") >= 1
    root_hits = sum(1 for r in fake_zotero.requests if r.url.path in ("/api/", "/api"))
    assert root_hits == 1
    client.server_info(refresh=True)
    root_hits = sum(1 for r in fake_zotero.requests if r.url.path in ("/api/", "/api"))
    assert root_hits == 2


def test_forbidden_names_the_setting_to_change():
    fake = FakeZotero(forbidden=True)
    with fake.client() as c:
        with pytest.raises(ZoteroForbidden) as excinfo:
            c.collections()
    assert "Allow other applications" in (excinfo.value.remedy or "")


def test_unreachable_is_distinct_from_forbidden():
    fake = FakeZotero(unreachable=True)
    with fake.client() as c:
        with pytest.raises(ZoteroUnreachable):
            c.collections()
        # server_info must answer rather than raise, so the status screen works
        assert c.server_info().reachable is False


def test_reads_ask_for_everything_in_one_response(client, fake_zotero):
    """Local API reads are unpaginated; limit=0 says 'no limit'."""
    client.collections()
    assert dict(fake_zotero.requests[-1].url.params)["limit"] == "0"


def test_children_never_returns_annotations(client):
    """The finding that a per-attachment lookup silently yields nothing.

    If this ever changes, the assertion below fails and the import can be
    simplified — but until then, code that trusts children() imports zero
    cards without erroring.
    """
    kids = client.children("ATT1")
    assert kids == []
    assert any(
        c.get("data", c)["itemType"] == "note" for c in client.children("SRC1")
    )


def test_annotation_index_is_one_request_keyed_by_attachment(client, fake_zotero):
    index = client.annotation_index()
    assert fake_zotero.count("itemType=annotation") == 1
    assert set(index) == {"ATT1", "ATT2", "ATT9"}
    assert [a.key for a in index["ATT1"]] == ["ANN6", "ANN3", "ANN1", "ANN2"]


def test_annotation_position_survives_both_shapes(client):
    index = client.annotation_index()
    pdf = next(a for a in index["ATT1"] if a.key == "ANN1")
    epub = index["ATT2"][0]
    assert pdf.position["pageIndex"] == 131
    assert epub.position["value"].startswith("epubcfi(")


def test_unknown_fields_survive_in_raw(client):
    from zkj.zotero.models import Source

    payload = next(
        p for p in client.collection_items_top("CH02") if p["data"]["key"] == "SRC1"
    )
    source = Source.from_payload(payload)
    assert source.raw["data"]["someFutureField"] == "must survive in raw"
    assert source.creators_short == "Smith"
    assert source.year == "2025"


def test_creators_and_year_degrade_honestly(client):
    from zkj.zotero.models import Source

    three = Source.from_payload(
        next(
            p
            for p in client.collection_items_top("CH03")
            if p["data"]["key"] == "SRC2"
        )
    )
    assert three.creators_short == "Tanaka et al."
    undated = Source(key="X", itemType="book", creators=[])
    assert undated.year is None
    assert undated.creators_short == "Anon."


def test_file_url_returns_none_when_there_is_no_file(client):
    assert client.file_url("ATT2").startswith("file://")
    assert client.file_url("ATT1") is None


def test_base_url_is_configurable():
    c = ZoteroClient(base="http://example.test/api/", user="7")
    assert c.prefix == "http://example.test/api/users/7"
