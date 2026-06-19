"""Work order state machine — Phase 3.3.

Defines work order lifecycle:
  DRAFT → PENDING_APPROVAL → APPROVED → BUILDING → SUCCESS/FAILED → CLOSED

Secondary review flow:
  PENDING_SECOND_REVIEW → SECOND_APPROVED / SECOND_REJECTED
"""

from enum import Enum
from typing import Optional


class WorkOrderState(str, Enum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    BUILDING = "BUILDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CLOSED = "CLOSED"

    # Secondary review states
    PENDING_SECOND_REVIEW = "PENDING_SECOND_REVIEW"
    SECOND_APPROVED = "SECOND_APPROVED"
    SECOND_REJECTED = "SECOND_REJECTED"


# Valid transitions (current → [next valid states])
TRANSITIONS: dict[WorkOrderState, list[WorkOrderState]] = {
    WorkOrderState.DRAFT: [
        WorkOrderState.PENDING_APPROVAL,
        WorkOrderState.CLOSED,
    ],
    WorkOrderState.PENDING_APPROVAL: [
        WorkOrderState.APPROVED,
        WorkOrderState.CLOSED,
    ],
    WorkOrderState.APPROVED: [
        WorkOrderState.BUILDING,
        WorkOrderState.PENDING_SECOND_REVIEW,
        WorkOrderState.CLOSED,
    ],
    WorkOrderState.BUILDING: [
        WorkOrderState.SUCCESS,
        WorkOrderState.FAILED,
    ],
    WorkOrderState.SUCCESS: [
        WorkOrderState.CLOSED,
    ],
    WorkOrderState.FAILED: [
        WorkOrderState.BUILDING,  # retry
        WorkOrderState.CLOSED,
    ],
    WorkOrderState.CLOSED: [],  # terminal

    # Secondary review
    WorkOrderState.PENDING_SECOND_REVIEW: [
        WorkOrderState.SECOND_APPROVED,
        WorkOrderState.SECOND_REJECTED,
    ],
    WorkOrderState.SECOND_APPROVED: [
        WorkOrderState.BUILDING,
        WorkOrderState.CLOSED,
    ],
    WorkOrderState.SECOND_REJECTED: [
        WorkOrderState.CLOSED,
    ],
}


class StateError(Exception):
    """Raised when an invalid state transition is attempted."""


def can_transition(current: WorkOrderState, target: WorkOrderState) -> bool:
    """Check if a state transition is valid."""
    return target in TRANSITIONS.get(current, [])


def transition(current: WorkOrderState, target: WorkOrderState) -> WorkOrderState:
    """Attempt a state transition. Raises StateError if invalid."""
    if not can_transition(current, target):
        raise StateError(
            f"Invalid transition: {current.value} → {target.value}"
        )
    return target


def is_terminal(state: WorkOrderState) -> bool:
    """True if the state is terminal (no further transitions)."""
    return TRANSITIONS.get(state, []) == []
