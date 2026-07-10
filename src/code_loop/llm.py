from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any, Protocol

from openai import OpenAI

from .config import ModelConfig, Price
from .models import Usage


class ChatClient(Protocol):
    def complete(self, *, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> "Completion": ...


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Completion:
    content: str | None
    tool_calls: list[ToolCall]
    usage: Usage


class DeepSeekClient:
    def __init__(self, config: ModelConfig):
        key = os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise RuntimeError("DEEPSEEK_API_KEY is required to contact DeepSeek")
        self.client = OpenAI(
            api_key=key,
            base_url=config.base_url,
            timeout=config.request_timeout_seconds,
            max_retries=config.max_retries,
        )

    def complete(self, *, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Completion:
        response = self.client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            response_format={"type": "json_object"},
            max_tokens=8_000,
        )
        message = response.choices[0].message
        usage = response.usage
        calls = []
        for call in message.tool_calls or []:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            calls.append(ToolCall(id=call.id, name=call.function.name, arguments=arguments))
        return Completion(
            content=message.content,
            tool_calls=calls,
            usage=Usage(
                prompt_cache_hit_tokens=getattr(usage, "prompt_cache_hit_tokens", 0) or 0,
                prompt_cache_miss_tokens=getattr(usage, "prompt_cache_miss_tokens", 0) or 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            ),
        )


def estimate_cost(usage: Usage, price: Price) -> float:
    return (
        usage.prompt_cache_hit_tokens * price.input_cache_hit
        + usage.prompt_cache_miss_tokens * price.input_cache_miss
        + usage.completion_tokens * price.output
    ) / 1_000_000
