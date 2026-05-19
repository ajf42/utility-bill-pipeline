"""Resolution Drafter output contract.

The Drafter is the single Anthropic API call on the DraftForHumanReview
triage route (DESIGN.md §4 "Resolution Drafter Service"). It produces a
structured proposal — a corrective action, a drafted email, and the
basis for both — that a human reviewer approves or rejects.

The drafter does not decide the route and does not write to any store.
This module defines only the output shape; the service that produces it
lives in [src/services/drafter.py](../services/drafter.py).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ProposedAction(str, Enum):
    """The categorical fix the drafter is proposing.

    The set is deliberately small and human-readable — these labels
    appear in the audit log and in the back-office review UI mockup.
    """

    CONVERT_UNIT = "CONVERT_UNIT"
    REQUEST_CLARIFICATION = "REQUEST_CLARIFICATION"
    APPLY_REFERENCE_CORRECTION = "APPLY_REFERENCE_CORRECTION"
    REJECT_DUPLICATE = "REJECT_DUPLICATE"
    ADJUST_PERIOD = "ADJUST_PERIOD"


class EmailRecipientType(str, Enum):
    """Who the drafted email is addressed to."""

    UTILITY_PROVIDER = "UTILITY_PROVIDER"
    PROPERTY_MANAGER = "PROPERTY_MANAGER"
    INTERNAL_TEAM = "INTERNAL_TEAM"


class DrafterOutput(BaseModel):
    """Structured output produced by the Resolution Drafter.

    ``proposed_correction`` is the machine-applicable partial override
    that the system will apply iff a human approves. An empty dict means
    no self-correction is safe — the drafter is asking for external
    information (email is the action; the system waits on the reply).
    ``basis_note`` and ``confidence_note`` make the drafter's reasoning
    auditable. Both are required; ``confidence_note`` uses the literal
    string "no uncertainty noted" when the drafter sees no uncertainty.
    """

    proposed_action: ProposedAction
    proposed_correction: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Partial field overrides to apply on human approval. Empty "
            "when no machine-applicable correction is safe."
        ),
    )
    draft_email_subject: str = Field(min_length=1)
    draft_email_body: str = Field(
        min_length=1,
        description="Body of the drafted email. Target length 100-400 words.",
    )
    draft_email_recipient_type: EmailRecipientType
    basis_note: str = Field(
        min_length=1,
        description=(
            "The drafter's reasoning: what it saw, what it inferred, why "
            "it chose this proposed_action. Target length 50-200 words."
        ),
    )
    confidence_note: str = Field(
        min_length=1,
        description=(
            "One sentence on what the drafter is uncertain about. "
            "Required; use 'no uncertainty noted' if none."
        ),
    )
