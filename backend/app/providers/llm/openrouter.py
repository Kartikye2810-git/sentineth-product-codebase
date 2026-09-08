import os
from typing import Any

from openai import AsyncOpenAI

from app.providers.llm.base import LLMProvider


class OpenRouterProvider(LLMProvider):
    def __init__(self) -> None:
        api_key = os.getenv("OPENROUTER_API_KEY")

        if not api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is not configured."
            )

        self._client = AsyncOpenAI(
            api_key=api_key,
            timeout=10.0,
            max_retries=2,
            base_url=os.getenv(
                "OPENROUTER_BASE_URL",
                "https://openrouter.ai/api/v1",
            ),
        )

        self._model = os.getenv(
            "OPENROUTER_LLM_MODEL",
            "openrouter/free",
        )

    async def generate(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> str:

        if not messages:
            raise ValueError(
                "At least one message is required."
            )

        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            **kwargs,
        )

        if not response.choices:
            raise ValueError(
                "LLM returned no choices."
            )

        content = response.choices[0].message.content

        if not content:
            raise ValueError(
                "LLM returned an empty response."
            )

        return content