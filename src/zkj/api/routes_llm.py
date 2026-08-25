"""Sending a prompt to Claude — off by default, and never the only way."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import llm
from .. import prompts as prompt_builder
from ..validate import save_draft, to_markdown, validate
from .deps import get_db
from .routes_writes import _project

router = APIRouter(tags=["llm"])


class KeyIn(BaseModel):
    key: str


class SendIn(BaseModel):
    kind: str = "paper"
    section_id: str | None = None
    mode: str = "draft"
    quoting: str = "model"
    effort: str = "high"
    save: bool = True


@router.get("/api/llm")
def get_llm() -> dict[str, Any]:
    return llm.availability().as_dict()


@router.put("/api/llm/key")
def put_key(body: KeyIn) -> dict[str, Any]:
    """Hold a key for this run only.

    It is not written to the database, not written to a file, and not logged.
    Restarting the workbench forgets it.
    """
    llm.set_session_key(body.key)
    state = llm.availability()
    if not state.ready:
        llm.set_session_key(None)
        raise HTTPException(422, state.reason)
    return state.as_dict()


@router.delete("/api/llm/key")
def delete_key() -> dict[str, Any]:
    llm.set_session_key(None)
    return llm.availability().as_dict()


@router.post("/api/projects/{project_id}/send")
def send_prompt(
    project_id: str,
    body: SendIn,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    """Build the prompt, post it, and check what comes back.

    The same prompt the Copy button would give you, and the same checks the
    paste-back box would run. Nothing about the evidence or the validation
    changes because a machine did the pasting.
    """
    project = _project(conn, project_id)
    try:
        prompt = prompt_builder.build(
            conn,
            project,
            body.kind,
            section_id=body.section_id,
            mode=body.mode,
            quoting=body.quoting,
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from e

    export_id = prompt_builder.store(conn, project_id, prompt)

    try:
        result = llm.send(prompt.content, effort=body.effort)
    except llm.LLMUnavailable as e:
        raise HTTPException(409, e.availability.as_dict()) from e
    except RuntimeError as e:
        raise HTTPException(502, str(e)) from e

    if result.refusal:
        return {
            "prompt": {"id": export_id, "chars": prompt.chars, "tokens": prompt.tokens},
            "llm": result.as_dict(),
            "validation": None,
            "draft": None,
            "markdown": None,
        }

    section_id = body.section_id if body.kind == "section" else None
    checked = validate(conn, project_id, section_id, result.text)
    saved = (
        save_draft(
            conn,
            project_id,
            section_id,
            result.text,
            prompt_export_id=export_id,
            validation=checked,
        )
        if body.save
        else None
    )
    return {
        "prompt": {"id": export_id, "chars": prompt.chars, "tokens": prompt.tokens},
        "llm": result.as_dict(),
        "validation": checked.as_dict(),
        "draft": {"id": saved["id"], "version": saved["version"]} if saved else None,
        "markdown": to_markdown(conn, project_id, section_id, result.text),
        "content": result.text,
    }
