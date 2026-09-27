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
        options: dict[str, Any] | None = None,
        format: str | dict[str, Any] | None = None,
        think: bool | None = None,
    ) -> ChatResponse:
        """Send a chat request to Ollama.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: Optional list of tool definitions in Ollama format.
            options: Ollama generation options, e.g. {"num_predict": 256}.
                Without a num_predict cap the model generates until it decides
                to stop, which on CPU-only hardware is unbounded wall time.
            format: "json" for any valid JSON, or a JSON Schema dict to constrain
                the shape itself. Prefer the schema: measured on this Pi, small
                models given only "json" either return empty objects
                (qwen2.5:1.5b) or enumerate every input and blow the token cap
                mid-object (gemma3:1b). The same models with a schema produced
                conformant, correctly-typed output in half the wall time.
            think: Override the configured thinking mode for this call.

        Returns:
            ChatResponse with content and/or tool calls.
        """
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "tools": tools or [],
            "keep_alive": self.config.keep_alive,
        }
        if options:
            kwargs["options"] = options
        if format:
            kwargs["format"] = format

        # Thinking models spend wall time on reasoning tokens that Ollama does
        # NOT count in eval_duration and does not return in the response, so it
        # is pure cost here. Measured on gemma4:e2b/Pi 5: 2.1 tok/s with
        # thinking vs 5.0 tok/s without.
        effective_think = self.config.think if think is None else think
        if effective_think is not None:
            kwargs["think"] = effective_think

        # Run synchronous ollama call in thread pool
        loop = asyncio.get_event_loop()
        try:
            response = await loop.run_in_executor(
                None, lambda: self._client.chat(**kwargs)
            )
        except TypeError:
            # Older ollama-python / server without think support: retry without it
            kwargs.pop("think", None)
            response = await loop.run_in_executor(
                None, lambda: self._client.chat(**kwargs)
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
        """Check if Ollama is reachable (not whether the model is available)."""
        try:
            loop = asyncio.get_event_loop()
            models = await loop.run_in_executor(None, self._client.list)
            # If we can list models, Ollama is reachable
            return True
        except Exception as e:
            # Log the error for debugging
            import logging
            logger = logging.getLogger(__name__)
            logger.debug(f"Ollama connection check failed: {type(e).__name__}: {e}")
            return False

    async def list_models(self) -> list[str]:
        """List available models."""
        try:
            loop = asyncio.get_event_loop()
            models_response = await loop.run_in_executor(None, self._client.list)

            # Handle different possible return types from ollama library
            if hasattr(models_response, 'models'):
                # If it's an object with .models attribute
                models = models_response.models
            elif isinstance(models_response, dict):
                # If it's a dict with 'models' key
                models = models_response.get("models", [])
            else:
                # If it's already a list
                models = models_response if isinstance(models_response, list) else []

            # Extract model names, handling both dict and object types
            model_list = []
            for m in models:
                if isinstance(m, dict):
                    model_list.append(m.get("name", m.get("model", "unknown")))
                elif hasattr(m, 'name'):
                    model_list.append(m.name)
                elif hasattr(m, 'model'):
                    model_list.append(m.model)

            return model_list
        except Exception as e:
            # Log the error for debugging
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to list Ollama models: {type(e).__name__}: {e}")
            return []

    async def pull_model(self, model: str) -> bool:
        """Pull a model via the ollama Python library.

        Args:
            model: Model name to pull (e.g. 'llama3.2:1b').

        Returns:
            True on success, False on failure.
        """
        try:
            import logging

            logger = logging.getLogger(__name__)
            logger.info(f"Pulling model '{model}' via ollama library...")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: self._client.pull(model))
            logger.info(f"Successfully pulled model '{model}'")
            return True
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(f"Failed to pull model '{model}': {type(e).__name__}: {e}")
            return False


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
