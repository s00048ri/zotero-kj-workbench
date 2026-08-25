"""The argument layer, the prompts it produces, and the drafts that come back."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from .. import prompts as prompt_builder
from ..compose import (
    add_claim,
    add_question,
    add_section,
    adopt_groups_as_sections,
    assign_card,
    choose_question,
    delete_section,
    list_claims,
    list_questions,
    list_sections,
    move_section,
    section_evidence,
    unassign_card,
    update_section,
)
from ..export import paper_markdown
from ..validate import save_draft, to_markdown, validate
from .deps import get_db
from .routes_writes import _project

router = APIRouter(prefix="/api/projects/{project_id}", tags=["compose"])


class QuestionIn(BaseModel):
    text: str
    rationale: str = ""
    origin: str = "mine"


class ClaimIn(BaseModel):
    text: str
    claim_type: str = "supporting"
    research_question_id: str | None = None


class SectionIn(BaseModel):
    title: str
    purpose: str = ""
    thesis: str = ""
    target_words: int | None = None
    parent_section_id: str | None = None


class SectionPatch(BaseModel):
    title: str | None = None
    purpose: str | None = None
    thesis: str | None = None
    target_words: int | None = None
    sort_order: int | None = None


class EvidenceIn(BaseModel):
    citation_mode: str = "paraphrase"
    argument_role: str = "evidence"
    user_instruction: str = ""
    include: bool = True


class PromptIn(BaseModel):
    kind: str
    section_id: str | None = None
    store: bool = True


class DraftIn(BaseModel):
    content: str
    prompt_export_id: str | None = None
    save: bool = True


# -- research questions ---------------------------------------------------


@router.get("/questions")
def get_questions(project_id: str, conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    _project(conn, project_id)
    return list_questions(conn, project_id)


@router.post("/questions")
def post_question(
    project_id: str, body: QuestionIn, conn: sqlite3.Connection = Depends(get_db)
) -> dict:
    _project(conn, project_id)
    try:
        return add_question(
            conn, project_id, body.text, rationale=body.rationale, origin=body.origin
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.post("/questions/{question_id}/choose")
def post_choose(
    project_id: str, question_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> dict:
    _project(conn, project_id)
    return choose_question(conn, project_id, question_id)


@router.delete("/questions/{question_id}")
def delete_question(
    project_id: str, question_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> dict:
    conn.execute(
        "DELETE FROM research_question WHERE id = ? AND project_id = ?",
        (question_id, project_id),
    )
    return {"deleted": True}


# -- claims ---------------------------------------------------------------


@router.get("/claims")
def get_claims(project_id: str, conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    _project(conn, project_id)
    return list_claims(conn, project_id)


@router.post("/claims")
def post_claim(
    project_id: str, body: ClaimIn, conn: sqlite3.Connection = Depends(get_db)
) -> dict:
    _project(conn, project_id)
    try:
        return add_claim(
            conn,
            project_id,
            body.text,
            claim_type=body.claim_type,
            research_question_id=body.research_question_id,
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.delete("/claims/{claim_id}")
def delete_claim(
    project_id: str, claim_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> dict:
    conn.execute("DELETE FROM claim WHERE id = ? AND project_id = ?", (claim_id, project_id))
    return {"deleted": True}


# -- sections and evidence ------------------------------------------------


@router.get("/sections")
def get_sections(project_id: str, conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    _project(conn, project_id)
    return list_sections(conn, project_id)


@router.post("/sections")
def post_section(
    project_id: str, body: SectionIn, conn: sqlite3.Connection = Depends(get_db)
) -> dict:
    _project(conn, project_id)
    try:
        return add_section(
            conn,
            project_id,
            body.title,
            purpose=body.purpose,
            thesis=body.thesis,
            target_words=body.target_words,
            parent_section_id=body.parent_section_id,
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.post("/sections/adopt-groups")
def post_adopt(
    project_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> dict[str, Any]:
    """Turn each group into a section, carrying its cards in as evidence."""
    _project(conn, project_id)
    made = adopt_groups_as_sections(conn, project_id)
    return {"created": len(made), "sections": made}


@router.post("/sections/{section_id}/move")
def post_move(
    project_id: str,
    section_id: str,
    delta: int = 1,
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    _project(conn, project_id)
    try:
        return move_section(conn, section_id, delta)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.patch("/sections/{section_id}")
def patch_section(
    project_id: str,
    section_id: str,
    body: SectionPatch,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    _project(conn, project_id)
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    return update_section(conn, section_id, changes)


@router.delete("/sections/{section_id}")
def remove_section(
    project_id: str, section_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> dict:
    _project(conn, project_id)
    delete_section(conn, section_id)
    return {"deleted": True}


@router.get("/sections/{section_id}/evidence")
def get_evidence(
    project_id: str, section_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> list[dict]:
    _project(conn, project_id)
    return [
        {
            "id": c["id"],
            "human_id": c["human_id"],
            "kind": c["kind"],
            "origin": c["origin"],
            "text": c["text"],
            "citation": c["citation"],
            "locator_estimated": bool(c["locator_estimated"]),
            "citation_mode": c["citation_mode"],
            "argument_role": c["argument_role"],
            "user_instruction": c["user_instruction"],
            "kj_path": c["kj_path"],
        }
        for c in section_evidence(conn, section_id, included_only=False)
    ]


@router.put("/sections/{section_id}/evidence/{card_id}")
def put_evidence(
    project_id: str,
    section_id: str,
    card_id: str,
    body: EvidenceIn,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    _project(conn, project_id)
    try:
        return assign_card(
            conn,
            section_id,
            card_id,
            citation_mode=body.citation_mode,
            argument_role=body.argument_role,
            user_instruction=body.user_instruction,
            include=body.include,
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.delete("/sections/{section_id}/evidence/{card_id}")
def delete_evidence(
    project_id: str,
    section_id: str,
    card_id: str,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    _project(conn, project_id)
    unassign_card(conn, section_id, card_id)
    return {"deleted": True}


# -- prompts --------------------------------------------------------------


@router.get("/prompts")
def prompt_availability(
    project_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> dict:
    _project(conn, project_id)
    return prompt_builder.available(conn, project_id)


@router.post("/prompts")
def post_prompt(
    project_id: str, body: PromptIn, conn: sqlite3.Connection = Depends(get_db)
) -> dict[str, Any]:
    project = _project(conn, project_id)
    try:
        prompt = prompt_builder.build(conn, project, body.kind, section_id=body.section_id)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    payload = prompt.as_dict()
    payload["id"] = prompt_builder.store(conn, project_id, prompt) if body.store else None
    return payload


@router.get("/prompt-exports")
def prompt_history(
    project_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> list[dict]:
    _project(conn, project_id)
    return [
        {
            "id": r["id"],
            "kind": r["kind"],
            "section_id": r["section_id"],
            "chars": r["chars"],
            "created_at": r["created_at"],
        }
        for r in conn.execute(
            "SELECT * FROM prompt_export WHERE project_id = ? "
            "ORDER BY created_at DESC LIMIT 30",
            (project_id,),
        )
    ]


# -- drafts ---------------------------------------------------------------


@router.post("/sections/{section_id}/draft")
def post_draft(
    project_id: str,
    section_id: str,
    body: DraftIn,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    """Check a draft against the evidence it was supposed to use."""
    _project(conn, project_id)
    if not body.content.strip():
        raise HTTPException(422, "Nothing pasted.")
    result = validate(conn, project_id, section_id, body.content)
    saved = (
        save_draft(
            conn,
            project_id,
            section_id,
            body.content,
            prompt_export_id=body.prompt_export_id,
            validation=result,
        )
        if body.save
        else None
    )
    return {
        "validation": result.as_dict(),
        "draft": {"id": saved["id"], "version": saved["version"]} if saved else None,
        "markdown": to_markdown(conn, project_id, section_id, body.content),
    }


@router.post("/draft")
def post_paper_draft(
    project_id: str,
    body: DraftIn,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    """Check a draft of the whole paper against every card in the project."""
    _project(conn, project_id)
    if not body.content.strip():
        raise HTTPException(422, "Nothing pasted.")
    result = validate(conn, project_id, None, body.content)
    saved = (
        save_draft(
            conn,
            project_id,
            None,
            body.content,
            prompt_export_id=body.prompt_export_id,
            validation=result,
        )
        if body.save
        else None
    )
    return {
        "validation": result.as_dict(),
        "draft": {"id": saved["id"], "version": saved["version"]} if saved else None,
        "markdown": to_markdown(conn, project_id, None, body.content),
    }


@router.get("/drafts")
def get_paper_drafts(
    project_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> list[dict]:
    _project(conn, project_id)
    return [
        {
            "id": r["id"],
            "version": r["version"],
            "created_at": r["created_at"],
            "content": r["content"],
        }
        for r in conn.execute(
            "SELECT * FROM draft WHERE project_id = ? AND section_id IS NULL "
            "ORDER BY version DESC",
            (project_id,),
        )
    ]


@router.get("/sections/{section_id}/drafts")
def get_drafts(
    project_id: str, section_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> list[dict]:
    _project(conn, project_id)
    return [
        {
            "id": r["id"],
            "version": r["version"],
            "created_at": r["created_at"],
            "content": r["content"],
            "validation": json.loads(r["validation_json"]) if r["validation_json"] else None,
        }
        for r in conn.execute(
            "SELECT * FROM draft WHERE section_id = ? ORDER BY version DESC",
            (section_id,),
        )
    ]


@router.get("/paper.md", response_class=PlainTextResponse)
def get_paper(project_id: str, conn: sqlite3.Connection = Depends(get_db)) -> str:
    _project(conn, project_id)
    return paper_markdown(conn, project_id)
