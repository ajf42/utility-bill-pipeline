"""Test doubles for external clients.

Kept deliberately small and shape-faithful: the fake mirrors the
attribute access pattern the real Anthropic SDK uses (`response.content`,
`block.type`, `block.input`) so service code never branches on "is this
real or a fake."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeContentBlock:
    """Mimics one entry in `anthropic.types.Message.content`.

    The real SDK uses `pydantic` models with `.type`, `.name`, `.input`
    for tool_use blocks. We only need attribute access — that's enough
    for the drafter parser.
    """

    type: str
    name: str | None = None
    input: Any = None
    text: str | None = None


@dataclass
class FakeMessage:
    """Mimics `anthropic.types.Message` to the extent the drafter reads it."""

    content: list[FakeContentBlock] = field(default_factory=list)
    stop_reason: str = "tool_use"


class _FakeMessages:
    def __init__(self, parent: "FakeAnthropicClient") -> None:
        self._parent = parent

    def create(self, **kwargs: Any) -> FakeMessage:
        self._parent.last_call_kwargs = kwargs
        if self._parent._next_response is None:
            raise AssertionError(
                "FakeAnthropicClient.messages.create called with no canned response set"
            )
        return self._parent._next_response


class FakeAnthropicClient:
    """Stands in for `anthropic.Anthropic` in tests.

    Configure the next response with ``set_next_response(response)``; the
    next call to ``messages.create(...)`` returns it and records the
    kwargs on ``last_call_kwargs``.
    """

    def __init__(self) -> None:
        self._next_response: FakeMessage | None = None
        self.last_call_kwargs: dict[str, Any] | None = None
        self.messages = _FakeMessages(self)

    def set_next_response(self, response: FakeMessage) -> None:
        self._next_response = response
