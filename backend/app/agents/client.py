"""The only module that talks to a language model.

One implementation over the official Groq SDK. Groq serves open-source models
(Llama, Qwen, GPT-OSS, Kimi) behind a hosted OpenAI-shaped chat-completions API
with native tool calling, so the surface this module exposes — a chat call that
may come back with tool calls — is unchanged from the local-Ollama design it
replaces. Nothing above this file knows which model or provider is in use;
switching is a change to `.env`.

Configuration is entirely environmental. `GROQ_API_KEY` is never defaulted and
never committed: a missing key fails immediately with an actionable message
rather than starting a run that silently degrades to the fallback controller.

Timeouts and retries live here rather than in the SDK's own retry layer so the
supervisor's latency budget is enforced by the code that owns it, and so a
retry is observable in the reasoning log.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any

from groq import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    Groq,
    InternalServerError,
    RateLimitError,
)

GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"
GROQ_API_KEY_ENV = "GROQ_API_KEY"
GROQ_MODEL_ENV = "GROQ_MODEL"

RETRYABLE = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)
RETRY_BACKOFF_SECONDS = 0.5

_MISSING_KEY_MESSAGE = (
    f"{GROQ_API_KEY_ENV} is not set. The Groq-backed supervisor cannot start "
    "without it.\n"
    "  1. Get a key at https://console.groq.com/keys\n"
    f"  2. Add '{GROQ_API_KEY_ENV}=gsk_...' to the .env file at the repository "
    "root, or export it in your shell\n"
    f"  3. Optionally set {GROQ_MODEL_ENV} (default: {GROQ_DEFAULT_MODEL})\n"
    "Runs under --controller=baseline or --controller=rule need no key."
)


class LLMConfigError(RuntimeError):
    """Configuration is missing or unusable. Raised before any request is sent."""


class LLMTransportError(RuntimeError):
    """The model could not be reached, or refused, after the retry budget."""


@dataclass(frozen=True)
class ChatResult:
    """One model response, including the accounting the dashboard reports."""

    content: str
    tool_calls: list[dict[str, Any]]
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    attempts: int = 1
    finish_reason: str = ""

    @property
    def raw_assistant_message(self) -> dict[str, Any]:
        """The assistant turn, shaped for appending back onto the conversation.

        The chat API requires the assistant message that requested tool calls to
        precede the tool results, and to carry the same call ids. Rebuilding it
        here keeps `llm.py` free of transport detail.
        """
        message: dict[str, Any] = {"role": "assistant", "content": self.content or None}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": call["arguments_raw"],
                    },
                }
                for call in self.tool_calls
            ]
        return message


class LLMClient:
    """Groq chat and tool-call transport with timeout, retry and accounting."""

    def __init__(
        self,
        base_url: str | None = None,   # None uses the SDK's Groq endpoint
        model: str | None = None,      # falls back to GROQ_MODEL, then the default
        api_key: str | None = None,    # falls back to GROQ_API_KEY; never defaulted
        timeout_seconds: float = 30.0,
        temperature: float = 0.2,
        retries: int = 2,
        max_completion_tokens: int = 1024,
        client: Any | None = None,     # injectable transport, for tests
    ) -> None:
        """Configure the transport.

        Args:
            base_url: Override the Groq API endpoint. Normally unset.
            model: Model id to serve. Falls back to `GROQ_MODEL`, then to
                `GROQ_DEFAULT_MODEL`.
            api_key: Credential. Falls back to `GROQ_API_KEY`. There is no
                default; a missing key raises `LLMConfigError`.
            timeout_seconds: Per-attempt wall-clock budget. The supervisor is on
                the control path, so a slow model must fail rather than stall.
            temperature: Sampling temperature. Low by default because a control
                decision should be reproducible, not creative.
            retries: Transport retries after the first attempt, for connection
                failures, timeouts, rate limits and 5xx. Never for a 4xx, which
                will fail identically however many times it is sent.
            max_completion_tokens: Ceiling on the reply. A policy plus a
                rationale is small; anything larger is a runaway generation.
            client: A pre-built transport. Supplied by the tests; production
                leaves it unset so the SDK is constructed from the environment.
        """
        if timeout_seconds <= 0:
            raise LLMConfigError(
                f"timeout_seconds must be positive, got {timeout_seconds}"
            )
        if retries < 0:
            raise LLMConfigError(f"retries must be non-negative, got {retries}")

        self.model = model or os.environ.get(GROQ_MODEL_ENV) or GROQ_DEFAULT_MODEL
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.max_completion_tokens = max_completion_tokens

        if client is not None:
            self._client = client
            return

        resolved_key = api_key or os.environ.get(GROQ_API_KEY_ENV) or ""
        if not resolved_key.strip():
            raise LLMConfigError(_MISSING_KEY_MESSAGE)

        options: dict[str, Any] = {
            "api_key": resolved_key,
            "timeout": timeout_seconds,
            # The SDK's own retry layer is disabled: retries are handled below so
            # that each attempt gets the full timeout and the count is reportable.
            "max_retries": 0,
        }
        if base_url:
            options["base_url"] = base_url
        self._client = Groq(**options)

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        """Send a conversation and return the model's reply and any tool calls.

        Args:
            messages: The conversation so far, in chat-completions format.
            tools: Tool definitions in OpenAI tool format, or None.

        Returns:
            The reply, its tool calls, token accounting and measured latency.

        Raises:
            LLMTransportError: The model was unreachable or refused, and the
                retry budget is spent. The supervisor turns this into a
                fallback decision rather than a failed run.
        """
        if not messages:
            raise ValueError("messages must not be empty")

        request: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_completion_tokens": self.max_completion_tokens,
        }
        if tools:
            request["tools"] = tools
            request["tool_choice"] = "auto"

        started = time.monotonic()
        last_error: Exception | None = None

        for attempt in range(1, self.retries + 2):
            try:
                response = self._client.chat.completions.create(**request)
            except RETRYABLE as exc:
                last_error = exc
                if attempt <= self.retries:
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                    continue
                break
            except APIStatusError as exc:
                # A 4xx is deterministic — a bad key, an unknown model, a
                # malformed request. Retrying it only wastes the latency budget.
                raise LLMTransportError(
                    f"Groq rejected the request ({exc.status_code}) for model "
                    f"{self.model!r}: {_status_detail(exc)}"
                ) from exc
            else:
                return self._to_result(
                    response, int((time.monotonic() - started) * 1000), attempt
                )

        raise LLMTransportError(
            f"Groq unreachable after {self.retries + 1} attempt(s) for model "
            f"{self.model!r}: {type(last_error).__name__}: {last_error}"
        ) from last_error

    def available(self) -> bool:
        """Report whether the endpoint is reachable and serving `model`.

        Used by `/api/health` and to decide up front whether a run should start
        under the supervisor or the fallback controller.
        """
        try:
            listing = self._client.models.list()
        except Exception:
            return False
        served = {
            getattr(entry, "id", None) or (entry.get("id") if isinstance(entry, dict) else None)
            for entry in (getattr(listing, "data", None) or [])
        }
        # An endpoint that answers but publishes no catalogue is still usable;
        # only a catalogue that positively excludes the model is a failure.
        return self.model in served if served else True

    # -- internals ----------------------------------------------------------

    def _to_result(self, response: Any, latency_ms: int, attempt: int) -> ChatResult:
        """Normalise an SDK response into a `ChatResult`."""
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise LLMTransportError("Groq returned no choices")
        message = choices[0].message
        usage = getattr(response, "usage", None)

        return ChatResult(
            content=getattr(message, "content", None) or "",
            tool_calls=_normalise_tool_calls(getattr(message, "tool_calls", None)),
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            latency_ms=latency_ms,
            attempts=attempt,
            finish_reason=getattr(choices[0], "finish_reason", "") or "",
        )


def parse_tool_arguments(raw: str) -> dict[str, Any]:
    """Parse a tool call's arguments, tolerating what models actually emit.

    Strict JSON first. Failing that, the two malformations that account for
    almost every real failure: the object wrapped in a fenced code block, and the
    object embedded in prose. Anything still unparseable raises with the reason,
    which becomes the self-correction message.

    Raises:
        ValueError: The arguments are not a JSON object.
    """
    text = (raw or "").strip()
    if not text:
        return {}

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        extracted = extract_json_object(text)
        if extracted is None:
            raise ValueError(
                f"arguments are not valid JSON ({exc.msg} at position {exc.pos}): "
                f"{_clip(text)}"
            ) from exc
        parsed = extracted

    if not isinstance(parsed, dict):
        raise ValueError(
            f"arguments must be a JSON object, got {type(parsed).__name__}: "
            f"{_clip(text)}"
        )
    return parsed


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Recover the first complete JSON object from free text, or None.

    A model told to emit only a tool call will occasionally emit the same JSON in
    its message content instead — correct reasoning, wrong envelope. Recovering
    it here converts a wasted round trip into a usable decision. Brace matching
    is string-aware so a brace inside the rationale cannot end the object early.
    """
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    candidates = [fenced.group(1)] if fenced else []
    candidates.append(text)

    for candidate in candidates:
        start = candidate.find("{")
        while start != -1:
            end = _matching_brace(candidate, start)
            if end is not None:
                try:
                    parsed = json.loads(candidate[start : end + 1])
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict):
                    return parsed
            start = candidate.find("{", start + 1)
    return None


def _matching_brace(text: str, start: int) -> int | None:
    """Index of the brace closing the one at `start`, ignoring braces in strings."""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _normalise_tool_calls(tool_calls: Any) -> list[dict[str, Any]]:
    """Flatten SDK tool calls, parsing arguments but never raising on bad JSON.

    A malformed argument object is data, not an exception: it has to reach the
    supervisor so the model can be told what was wrong with it.
    """
    normalised: list[dict[str, Any]] = []
    for index, call in enumerate(tool_calls or []):
        function = getattr(call, "function", None)
        raw = getattr(function, "arguments", "") or ""
        record: dict[str, Any] = {
            "id": getattr(call, "id", None) or f"call_{index}",
            "name": getattr(function, "name", "") or "",
            "arguments_raw": raw,
            "arguments": {},
            "parse_error": None,
        }
        try:
            record["arguments"] = parse_tool_arguments(raw)
        except ValueError as exc:
            record["parse_error"] = str(exc)
        normalised.append(record)
    return normalised


def _status_detail(exc: APIStatusError) -> str:
    """The useful part of an API error, without dumping a whole HTTP response."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
    return _clip(str(exc))


def _clip(text: str, limit: int = 200) -> str:
    """Bound a message so an error stays readable in a log line."""
    return text if len(text) <= limit else text[: limit - 3] + "..."
