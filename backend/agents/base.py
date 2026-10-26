"""Agent base class.

An agent is a system prompt plus an output contract. It asks the router for
text, parses it, and validates it against a Pydantic model. If validation
fails it retries exactly once with a blunter reminder appended - and because
that retry goes back through the router, it may well land on a different
provider, which is often what fixes it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from ..providers.router import ProviderRouter
from .parsing import ParseError, extract_json

# Appended on the retry. Deliberately blunt: the polite version is already in
# every system prompt, so repeating it adds nothing.
STRICTER_REMINDER = (
    "\n\nCRITICAL: Your previous response could not be parsed. "
    "Respond with a single raw JSON object and NOTHING else. "
    "No markdown code fences. No explanation before or after. "
    "No trailing commas. Start your response with { and end it with }."
)


class AgentError(Exception):
    """The agent could not produce valid output even after a retry."""


@dataclass
class AgentRun:
    output: BaseModel
    provider: str
    model: str
    latency_ms: int
    log_ids: list[int] = field(default_factory=list)
    attempts: int = 1


class Agent(ABC):
    name: str = "agent"
    system_prompt: str = ""
    schema: type[BaseModel] = BaseModel

    def __init__(self, router: ProviderRouter):
        self.router = router

    @abstractmethod
    def build_user_prompt(self, **inputs: Any) -> str:
        ...

    def run(self, **inputs: Any) -> AgentRun:
        user_prompt = self.build_user_prompt(**inputs)
        log_ids: list[int] = []
        last_error: Exception | None = None

        for attempt in (1, 2):
            system_prompt = self.system_prompt
            if attempt == 2:
                system_prompt += STRICTER_REMINDER

            # A provider failure (AllProvidersFailed) is not ours to handle -
            # it propagates to the orchestrator, which owns degradation.
            result = self.router.generate(system_prompt, user_prompt)
            log_ids.extend(result.log_ids)

            try:
                payload = extract_json(result.text)
                output = self.schema.model_validate(payload)
            except (ParseError, ValidationError) as exc:
                last_error = exc
                continue

            return AgentRun(
                output=output,
                provider=result.provider,
                model=result.model,
                latency_ms=result.latency_ms,
                log_ids=log_ids,
                attempts=attempt,
            )

        raise AgentError(
            f"{self.name}: no valid {self.schema.__name__} after 2 attempts "
            f"({type(last_error).__name__}: {last_error})"
        )
