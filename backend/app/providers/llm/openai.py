import os
from typing import Any

from openai import AsyncOpenAI

from app.providers.llm.base import LLMProvider


class OpenAIProvider(LLMProvider):
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        organization: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is required to use OpenAIProvider.")

        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.base_url = base_url
        self.organization = organization
        kwargs.setdefault("timeout", 10.0)
        kwargs.setdefault("max_retries", 2)
        self._client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            organization=self.organization,
            **kwargs,
        )

    async def generate(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> str:
        if not messages:
            raise ValueError("At least one message is required.")

        response = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            **kwargs,
        )

        return response.choices[0].message.content or ""
