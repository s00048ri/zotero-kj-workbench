"""Sending a prompt to Claude, for the researcher who would rather not paste.

This is off unless it is turned on, and it changes nothing else: the same
prompt is built, the same draft comes back, and the same validator checks it
against the same evidence. Copying the text into a chat remains the primary
path — it costs nothing, it needs no key, and it is the only way to see
exactly what is being sent before it goes.

Credentials are never written to disk by this app. They come from the
environment the server was started in, from an ``ant auth login`` profile, or
from a key the researcher hands the running process, which lives in memory and
dies with it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

MODEL = "claude-opus-5"
# Streaming, so a paper-length answer cannot trip an HTTP timeout.
MAX_TOKENS = 64_000
# Per million tokens, for showing what a send cost. Not billing.
PRICE_IN = 5.0
PRICE_OUT = 25.0

# Set by the researcher for this process only. Never persisted, never logged.
_session_key: str | None = None


def set_session_key(key: str | None) -> None:
    global _session_key
    _session_key = (key or "").strip() or None


def has_session_key() -> bool:
    return _session_key is not None


@dataclass
class Availability:
    ready: bool
    reason: str
    model: str = MODEL
    source: str | None = None
    base_url: str | None = None
    remedy: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "reason": self.reason,
            "model": self.model,
            "source": self.source,
            "base_url": self.base_url,
            "remedy": self.remedy,
        }


@dataclass
class LLMResult:
    text: str
    model: str
    stop_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    refusal: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def cost(self) -> float:
        return (
            self.input_tokens * PRICE_IN + self.output_tokens * PRICE_OUT
        ) / 1_000_000

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "stop_reason": self.stop_reason,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost, 4),
            "refusal": self.refusal,
            "warnings": self.warnings,
        }


class LLMUnavailable(RuntimeError):
    """No way to send, and the message says what would make one."""

    def __init__(self, availability: Availability) -> None:
        super().__init__(availability.reason)
        self.availability = availability


def availability() -> Availability:
    """Whether a prompt could be sent right now, and if not, what is missing."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return Availability(
            ready=False,
            reason="The Anthropic SDK is not installed.",
            remedy='Install it with: uv pip install -e ".[llm]" — or keep '
            "copying the prompt into a chat, which needs nothing.",
        )

    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    if _session_key:
        return Availability(
            ready=True,
            reason="Ready, with the key you gave this session.",
            source="a key held in memory for this run only",
            base_url=base_url,
        )
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return Availability(
            ready=True,
            reason="Ready, using the credentials this server was started with.",
            source="the environment",
            base_url=base_url,
        )

    # An unset ANTHROPIC_API_KEY does not mean there are no credentials — a
    # stored `ant auth login` profile is resolved by the SDK on its own. But
    # constructing a client succeeds either way, so the client is asked what
    # it would actually send: no auth header, no credentials.
    import anthropic

    try:
        resolved = bool(anthropic.Anthropic().auth_headers)
    except Exception:
        resolved = False
    if resolved:
        return Availability(
            ready=True,
            reason="Ready, using a stored Anthropic profile.",
            source="an `ant auth login` profile",
            base_url=base_url,
        )
    return Availability(
        ready=False,
        reason="No Anthropic credentials are available to this process.",
        base_url=base_url,
        remedy="Either run `ant auth login`, or start the workbench with "
        "ANTHROPIC_API_KEY set, or give this run a key below — it is kept in "
        "memory and never written to disk.",
    )


def send(prompt: str, *, effort: str = "high") -> LLMResult:
    """Post one prompt and wait for the whole answer.

    Streamed, because a paper is long and a non-streaming request of this size
    hits the SDK's timeout rather than finishing.
    """
    state = availability()
    if not state.ready:
        raise LLMUnavailable(state)

    import anthropic

    client = (
        anthropic.Anthropic(api_key=_session_key)
        if _session_key
        else anthropic.Anthropic()
    )

    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            message = stream.get_final_message()
    except anthropic.AuthenticationError as e:
        raise LLMUnavailable(
            Availability(
                ready=False,
                reason="Anthropic refused those credentials.",
                remedy="Check the key, or run `ant auth login`.",
            )
        ) from e
    except anthropic.RateLimitError as e:
        retry = e.response.headers.get("retry-after", "60")
        raise RuntimeError(
            f"Anthropic is rate-limiting this account. Try again in {retry}s, "
            f"or copy the prompt into a chat instead."
        ) from e
    except anthropic.APIConnectionError as e:
        raise RuntimeError(
            "Could not reach Anthropic. The prompt is unchanged and can be "
            "copied into a chat instead."
        ) from e
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"Anthropic returned {e.status_code}: {e.message}") from e

    text = "".join(
        block.text for block in message.content if getattr(block, "type", "") == "text"
    )
    warnings: list[str] = []
    if message.stop_reason == "max_tokens":
        warnings.append(
            "The answer hit the length limit and stops mid-thought. Ask for one "
            "section at a time, or paste the prompt into a chat where you can "
            "say “continue”."
        )
    refusal = None
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        refusal = getattr(details, "explanation", None) or "Claude declined this request."

    return LLMResult(
        text=text,
        model=message.model,
        stop_reason=message.stop_reason,
        input_tokens=message.usage.input_tokens,
        output_tokens=message.usage.output_tokens,
        refusal=refusal,
        warnings=warnings,
    )
