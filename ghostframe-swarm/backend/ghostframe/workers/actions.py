"""The closed set of actions a worker can take per deliberation.

The model must answer with one JSON object; `parse_action` validates it into
this discriminated union. Anything else bounces back as a validation error
for one self-correction attempt.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter, ValidationError


class UseTool(BaseModel):
    action: Literal["use_tool"] = "use_tool"
    tool: str
    args: dict = Field(default_factory=dict)
    reasoning: str = ""
    confidence: float = 0.7


class SubmitWork(BaseModel):
    action: Literal["submit"] = "submit"
    summary: str
    artifacts: dict = Field(default_factory=dict)  # e.g. {"verdict": "approve", ...}
    confidence: float = 0.7


class RejectWork(BaseModel):
    action: Literal["reject"] = "reject"
    reasons: list[str]
    confidence: float = 0.7


class AskClarification(BaseModel):
    action: Literal["ask"] = "ask"
    question: str
    confidence: float = 0.7


class Escalate(BaseModel):
    action: Literal["escalate"] = "escalate"
    question: str
    options: list[str] = Field(default_factory=list)
    confidence: float = 0.7


Action = Annotated[
    Union[UseTool, SubmitWork, RejectWork, AskClarification, Escalate],
    Field(discriminator="action"),
]

_adapter: TypeAdapter[Action] = TypeAdapter(Action)

ACTION_SCHEMA_DOC = """You must respond with exactly one JSON object choosing one action:
- {"action": "use_tool", "tool": "<name>", "args": {...}, "reasoning": "...", "confidence": 0.0-1.0}
- {"action": "submit", "summary": "...", "artifacts": {...}, "confidence": 0.0-1.0}
- {"action": "reject", "reasons": ["..."], "confidence": 0.0-1.0}   (refuse assigned work with reasons)
- {"action": "ask", "question": "...", "confidence": 0.0-1.0}       (ask the requester a clarifying question)
- {"action": "escalate", "question": "...", "options": ["..."], "confidence": 0.0-1.0}  (a human must decide)
"""


def parse_action(data: dict) -> Action:
    return _adapter.validate_python(data)


__all__ = [
    "ACTION_SCHEMA_DOC",
    "Action",
    "AskClarification",
    "Escalate",
    "RejectWork",
    "SubmitWork",
    "UseTool",
    "ValidationError",
    "parse_action",
]
