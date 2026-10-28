"""OpenAI-compatible LLM client defaulting to DeepSeek API with token & cost calculation."""

from typing import Protocol
from openai import OpenAI
from pydantic import BaseModel
from agent_forge.config import ForgeConfig


class LLMResponse(BaseModel):
    """Normalized LLM completion payload with token and cost accounting."""

    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


class LLMClient(Protocol):
    """Protocol for swappable LLM backends."""

    def complete(self, system_prompt: str, user_prompt: str) -> LLMResponse: ...


class OpenAICompatibleClient:
    """Client for DeepSeek, OpenAI, or local OpenAI-compatible endpoints."""

    def __init__(self, config: ForgeConfig):
        self.config = config
        self._client = OpenAI(
            api_key=config.llm_api_key or "sk-placeholder",
            base_url=config.llm_base_url,
        )

    def complete(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        resp = self._client.chat.completions.create(
            model=self.config.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
        )
        content = resp.choices[0].message.content or ""
        usage = resp.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        cost_usd = (
            (prompt_tokens / 1_000_000.0) * self.config.prompt_price_per_1m
            + (completion_tokens / 1_000_000.0) * self.config.completion_price_per_1m
        )
        return LLMResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=round(cost_usd, 6),
        )
