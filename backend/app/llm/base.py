from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

class LLMResponse(BaseModel):
    content: str
    usage: Dict[str, int]

class LLMProvider(ABC):
    @abstractmethod
    async def generate(self, prompt: str, model: str, **kwargs) -> LLMResponse:
        pass

    @abstractmethod
    def validate_model(self, model: str) -> bool:
        pass
