"""Resolution Drafter Service — the single Anthropic API call.

Called by triage on the DraftForHumanReview route (DESIGN.md §4
"Resolution Drafter Service" and §5 — the unit-mismatch case is the
demo highlight). The service is pure: a ValidatedBill plus context in,
a DrafterOutput out. It does not write to any store, does not decide
the route, and does not retry on API errors.

Structured output is forced via Anthropic's tool-use mechanism — see
ADR-010. Parse failures raise ``DrafterParseError`` (fail-loud — see
ADR-011).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anthropic

from src.models.bill import ValidatedBill
from src.models.drafter import DrafterOutput
from src.models.entities import Meter, Reading

_DEFAULT_SYSTEM_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "drafter_system.md"
_TOOL_NAME = "draft_resolution"
_TOOL_DESCRIPTION = (
    "Emit a structured drafted resolution (proposed action, draft email, "
    "and basis note) for a flagged utility bill. Always call this tool; "
    "never respond outside it."
)


class DrafterParseError(RuntimeError):
    """Raised when the model's response cannot be parsed into a DrafterOutput.

    The raw Anthropic response is attached on ``raw_response`` so the
    audit log can record exactly what came back. Per ADR-011 the
    service does not retry or degrade — failure is loud and visible.
    """

    def __init__(self, message: str, raw_response: Any) -> None:
        super().__init__(message)
        self.raw_response = raw_response


def build_drafter_user_message(
    bill: ValidatedBill,
    meter: Meter,
    history: list[Reading],
) -> str:
    """Render the user message the drafter sees.

    Module-level (not a method) so tests can pin the message shape
    without constructing a full service. Plain text with section
    headers — the model handles structured prose more reliably than
    a JSON dump of nested pydantic objects.
    """
    payload = bill.raw_payload
    flag_lines = (
        "\n".join(
            f"- {f.type.value} ({f.severity.value}): {f.description} "
            f"[recommended: {f.recommended_action}]"
            for f in bill.flags
        )
        or "- (none)"
    )

    history_lines = (
        "\n".join(
            f"- period {r.period_start.isoformat()} to {r.period_end.isoformat()}: "
            f"{r.usage} {r.usage_units.value}"
            + (f", cost {r.cost} {r.currency}" if r.cost is not None else "")
            for r in history[-6:]
        )
        or "- (no prior readings on this meter)"
    )

    return (
        "## Incoming bill (raw payload)\n"
        f"- site_name: {payload.get('site_name')}\n"
        f"- account_number: {payload.get('account_number')}\n"
        f"- meter_id_string: {payload.get('meter_id_string')}\n"
        f"- period_start: {payload.get('period_start')}\n"
        f"- period_end: {payload.get('period_end')}\n"
        f"- usage: {payload.get('usage')}\n"
        f"- usage_units: {payload.get('usage_units')}\n"
        f"- cost: {payload.get('cost')}\n"
        f"- currency: {payload.get('currency')}\n"
        f"- canonical_provider: {bill.canonical_provider}\n"
        "\n"
        "## Matched meter (ground truth for unit/currency)\n"
        f"- meter_id_string: {meter.meter_id_string}\n"
        f"- locked unit: {meter.unit.value}\n"
        f"- locked currency: {meter.currency}\n"
        f"- meter type: {meter.type.value}\n"
        f"- active: {meter.active}\n"
        "\n"
        "## Quality flags raised by validation\n"
        f"{flag_lines}\n"
        "\n"
        "## Recent readings on this meter (most recent last)\n"
        f"{history_lines}\n"
        "\n"
        "Call the draft_resolution tool with your structured proposal."
    )


class DrafterService:
    """Wraps a single Anthropic tool-use call.

    The Anthropic client is injected so tests can pass a fake (see
    [tests/fakes.py](../../tests/fakes.py)). Real production wiring
    constructs `anthropic.Anthropic()` from `ANTHROPIC_API_KEY` and
    passes it in.
    """

    def __init__(
        self,
        client: anthropic.Anthropic,
        model: str = "claude-sonnet-4-6",
        system_prompt_path: Path = _DEFAULT_SYSTEM_PROMPT_PATH,
        max_tokens: int = 1024,
    ) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self._system_prompt = Path(system_prompt_path).read_text(encoding="utf-8")
        self._tool_schema = DrafterOutput.model_json_schema()

    def draft(
        self,
        validated_bill: ValidatedBill,
        meter: Meter,
        prior_readings: list[Reading],
    ) -> DrafterOutput:
        user_message = build_drafter_user_message(validated_bill, meter, prior_readings)
        tool = {
            "name": _TOOL_NAME,
            "description": _TOOL_DESCRIPTION,
            "input_schema": self._tool_schema,
        }
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=self._system_prompt,
            tools=[tool],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": user_message}],
        )
        return _parse_tool_use(response)


def _parse_tool_use(response: Any) -> DrafterOutput:
    content = getattr(response, "content", None)
    if not content:
        raise DrafterParseError("response has no content blocks", response)
    for block in content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == _TOOL_NAME:
            tool_input = getattr(block, "input", None)
            if not isinstance(tool_input, dict):
                raise DrafterParseError(
                    f"tool_use block has non-dict input: {type(tool_input).__name__}",
                    response,
                )
            try:
                return DrafterOutput.model_validate(tool_input)
            except Exception as exc:  # pydantic.ValidationError, etc.
                raise DrafterParseError(
                    f"tool_use input failed DrafterOutput validation: {exc}",
                    response,
                ) from exc
    raise DrafterParseError(
        f"no tool_use block named {_TOOL_NAME!r} in response", response
    )
