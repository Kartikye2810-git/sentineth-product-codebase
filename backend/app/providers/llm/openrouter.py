from typing import Any

from openai import AsyncOpenAI

from app.observability import capture_usage
from app.providers.llm.base import LLMProvider
from app.settings import get_settings


class OpenRouterProvider(LLMProvider):
    def __init__(self) -> None:
        api_key = get_settings().openrouter_api_key.get_secret_value()

        if not api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is not configured."
            )

        self._client = AsyncOpenAI(
            api_key=api_key,
            timeout=10.0,
            max_retries=2,
            base_url=get_settings().openrouter_base_url,
        )

        self._model = get_settings().openrouter_llm_model

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

        capture_usage(response, self._model)

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