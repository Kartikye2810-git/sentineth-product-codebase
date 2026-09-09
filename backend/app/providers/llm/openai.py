from typing import Any

from openai import AsyncOpenAI

from app.observability import capture_usage
from app.providers.llm.base import LLMProvider
from app.settings import get_settings


class OpenAIProvider(LLMProvider):
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        organization: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.api_key = api_key or get_settings().openai_api_key.get_secret_value()
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is required to use OpenAIProvider.")

        self.model = model or get_settings().openai_model
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

        capture_usage(response, self.model)
        return response.choices[0].message.content or ""
