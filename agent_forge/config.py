"""Configuration models for Agent Forge."""

import os
from pydantic import BaseModel, Field


class ForgeConfig(BaseModel):
    """Safety, budget, retry, and LLM configuration for Agent Forge."""

    max_attempts: int = 5
    max_files_changed: int = 5
    max_diff_lines: int = 200
    max_total_tokens: int = 100_000
    max_cost_usd: float = 0.50
    test_timeout_seconds: int = 60
    consecutive_error_reflect_threshold: int = 2

    llm_base_url: str = Field(
        default_factory=lambda: os.environ.get("FORGE_LLM_BASE_URL", "https://api.deepseek.com")
    )
    llm_model: str = Field(
        default_factory=lambda: os.environ.get("FORGE_LLM_MODEL", "deepseek-chat")
    )
    llm_api_key: str = Field(
        default_factory=lambda: os.environ.get("DEEPSEEK_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
    )
    prompt_price_per_1m: float = 0.27
    completion_price_per_1m: float = 1.10
