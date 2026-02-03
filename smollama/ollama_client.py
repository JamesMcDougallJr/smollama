"""Async wrapper around the Ollama library for LLM interactions."""

import asyncio
from dataclasses import dataclass
from typing import Any

import ollama

from .config import OllamaConfig


@dataclass
class ToolCall:
    """Represents a tool call from the LLM."""

    name: str
    arguments: dict[str, Any]


@dataclass
class ChatResponse:
    """Response from the LLM."""

    content: str | None
    tool_calls: list[ToolCall]
    done: bool

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class OllamaClient:
    """Async client for interacting with Ollama."""

    def __init__(self, config: OllamaConfig):
        self.config = config
        self._client = ollama.Client(host=config.base_url)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResponse:
        """Send a chat request to Ollama.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: Optional list of tool definitions in Ollama format.

        Returns:
            ChatResponse with content and/or tool calls.
        """
        # Run synchronous ollama call in thread pool
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._client.chat(
                model=self.config.model,
                messages=messages,
                tools=tools or [],
            ),
        )

        # Parse tool calls from response
        tool_calls = []
        if "message" in response and "tool_calls" in response["message"]:
            for tc in response["message"]["tool_calls"]:
                tool_calls.append(
                    ToolCall(
                        name=tc["function"]["name"],
                        arguments=tc["function"]["arguments"],
                    )
                )

        content = None
        if "message" in response and "content" in response["message"]:
            content = response["message"]["content"]

        return ChatResponse(
            content=content,
            tool_calls=tool_calls,
            done=response.get("done", True),
        )

    async def check_connection(self) -> bool:
        """Check if Ollama is reachable and the model is available."""
        try:
            loop = asyncio.get_event_loop()
            models = await loop.run_in_executor(None, self._client.list)
            model_names = [m["name"] for m in models.get("models", [])]
            # Check if our configured model (or base name) is available
            base_model = self.config.model.split(":")[0]
            return any(base_model in name for name in model_names)
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        """List available models."""
        try:
            loop = asyncio.get_event_loop()
            models = await loop.run_in_executor(None, self._client.list)
            return [m["name"] for m in models.get("models", [])]
        except Exception:
            return []


def format_tool_result(tool_name: str, result: Any) -> dict[str, Any]:
    """Format a tool result for inclusion in messages.

    Args:
        tool_name: Name of the tool that was called.
        result: Result from tool execution.

    Returns:
        Message dict in Ollama tool response format.
    """
    return {
        "role": "tool",
        "content": str(result),
    }


def format_assistant_tool_calls(tool_calls: list[ToolCall]) -> dict[str, Any]:
    """Format assistant message with tool calls.

    Args:
        tool_calls: List of tool calls made by the assistant.

    Returns:
        Message dict representing the assistant's tool call request.
    """
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "function": {
                    "name": tc.name,
                    "arguments": tc.arguments,
                }
            }
            for tc in tool_calls
        ],
    }
