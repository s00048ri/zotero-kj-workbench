"""The preflight check: what a fixture cannot tell the truth about.

These tests can only prove that the check reports correctly, not that this
machine is sound — that is the whole point of the command. What they pin down
is the reporting, because a preflight that fails silently or crashes on the
first bad answer is worse than none.
"""

from __future__ import annotations

import pytest

from tests.conftest import FakeZotero
from zkj import config, doctor


@pytest.fixture
def here(tmp_path, monkeypatch):
    """Keep the database and the staging probe inside the test's own directory."""
    monkeypatch.setattr(
        config, "settings", config.Settings(db_path=str(tmp_path / "zkj.sqlite3"))
    )
    for module in (doctor,):
        monkeypatch.setattr(module, "settings", config.settings)
    return tmp_path


def by_name(checks):
    return {c.name: c for c in checks}


def test_a_sound_machine_passes_everything(here):
    pdf = here / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    fake = FakeZotero()
    fake.data["files"] = {"ATT1": pdf.as_uri(), "ATT2": pdf.as_uri()}

    checks = doctor.run(fake.client())

    assert all(c.ok for c in checks), [c.detail for c in checks if not c.ok]
    named = by_name(checks)
    assert "Zotero 10" in named["Zotero will accept notes"].detail or named[
        "Zotero will accept notes"
    ].detail.startswith("Server ID")
    assert "sampled attachments resolved" in named["Attachment files resolve"].detail


def test_an_unresolvable_attachment_reports_what_zotero_said(here):
    """The failure worth optimising for: the file URL is printed, because that
    line is what turns 'it does not work' into a fix."""
    fake = FakeZotero()
    fake.data["files"] = {"ATT1": "file:///nowhere/that/exists/paper.pdf"}

    check = by_name(doctor.run(fake.client()))["Attachment files resolve"]

    assert check.ok is False
    assert "/nowhere/that/exists/paper.pdf" in " ".join(check.notes)
    assert "bug in how the workbench turns it into a path" in check.remedy


def test_a_partly_synced_library_passes_and_says_so(here):
    pdf = here / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    fake = FakeZotero()
    fake.data["files"] = {"ATT1": pdf.as_uri()}  # ATT2 was never downloaded

    check = by_name(doctor.run(fake.client()))["Attachment files resolve"]

    assert check.ok is True
    assert "of" in check.detail and "resolved" in check.detail
    assert "not downloaded" in check.remedy


def test_zotero_being_down_does_not_stop_the_rest(here):
    """A preflight that gives up on the first failure tells you one thing when
    it could have told you four."""
    checks = doctor.run(FakeZotero(unreachable=True).client())
    named = by_name(checks)

    assert named["Zotero is answering"].ok is False
    assert "Start Zotero" in named["Zotero is answering"].remedy
    # the questions that do not need Zotero are still answered
    assert named["The workbench database"].ok is True
    assert named["The staging folder can be built"].ok is True
    # and the ones that do are not guessed at
    assert "Attachment files resolve" not in named


def test_an_old_zotero_fails_only_the_write_check(here):
    pdf = here / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    fake = FakeZotero(headers={"Zotero-API-Version": "3"})
    fake.data["files"] = {"ATT1": pdf.as_uri(), "ATT2": pdf.as_uri()}

    named = by_name(doctor.run(fake.client()))

    assert named["Zotero is answering"].ok is True
    assert named["Zotero will accept notes"].ok is False
    assert "older than 10" in named["Zotero will accept notes"].detail
    assert named["The library reads"].ok is True


def test_forbidden_says_which_setting_to_turn_on(here):
    check = by_name(doctor.run(FakeZotero(forbidden=True).client()))["Zotero is answering"]
    assert check.ok is False
    assert "Allow other applications" in check.remedy


def test_the_report_carries_every_remedy(here):
    checks = doctor.run(FakeZotero(unreachable=True).client())
    text = doctor.report(checks)

    assert "FAIL  Zotero is answering" in text
    assert "→ Start Zotero" in text
    assert "1 of 3 checks failed" in text


def test_a_clean_report_says_what_it_does_not_cover(here):
    pdf = here / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    fake = FakeZotero()
    fake.data["files"] = {"ATT1": pdf.as_uri(), "ATT2": pdf.as_uri()}

    text = doctor.report(doctor.run(fake.client()))

    assert "in order" in text
    # the one thing it cannot prove is said out loud rather than implied
    assert "only provable by uploading one" in text
