"""The only module that talks to a language model.

One OpenAI-compatible implementation covers Ollama and any self-hosted
OpenAI-compatible endpoint — they differ solely by `base_url`. That is what
makes the system model-agnostic without an adapter hierarchy: pointing at a
different server is a configuration change, not a code change.

Implemented in Milestone 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChatResult:
    """One model response, including the accounting the dashboard reports."""

    content: str
    tool_calls: list[dict[str, Any]]
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


class LLMClient:
    """OpenAI-compatible chat and tool-call transport with timeout and retry."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434/v1",
        model: str = "qwen2.5:7b-instruct",
        api_key: str = "ollama",       # Ollama ignores it; hosted endpoints need it
        timeout_seconds: float = 30.0,
        temperature: float = 0.2,
    ) -> None:
        raise NotImplementedError("Milestone 2")

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        """Send a conversation and return the model's reply and any tool calls."""
        raise NotImplementedError("Milestone 2")

    def available(self) -> bool:
        """Report whether the endpoint is reachable and serving `model`.

        Used by `/api/health` and to decide up front whether a run should start
        under the supervisor or the fallback controller.
        """
        raise NotImplementedError("Milestone 2")
