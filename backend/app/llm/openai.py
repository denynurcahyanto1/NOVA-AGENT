import openai
from typing import Dict
from .base import LLMProvider, LLMResponse
from tenacity import retry, stop_after_attempt, wait_exponential

class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str):
        self.client = openai.AsyncOpenAI(api_key=api_key)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def generate(self, prompt: str, model: str, **kwargs) -> LLMResponse:
        response = await self.client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            **kwargs
        )
        return LLMResponse(
            content=response.choices[0].message.content,
            usage=dict(response.usage)
        )

    def validate_model(self, model: str) -> bool:
        # Simplified validation
        return model.startswith("gpt-")
