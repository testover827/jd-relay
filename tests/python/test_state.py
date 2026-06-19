"""Tests for work order state machine."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import pytest
from forwarder.state import (
    WorkOrderState, TRANSITIONS, can_transition, transition, is_terminal, StateError,
)


class TestStateMachine:

    def test_valid_transitions(self):
        """Verify core happy-path transitions."""
        assert can_transition(WorkOrderState.DRAFT, WorkOrderState.PENDING_APPROVAL)
        assert can_transition(WorkOrderState.PENDING_APPROVAL, WorkOrderState.APPROVED)
        assert can_transition(WorkOrderState.APPROVED, WorkOrderState.BUILDING)
        assert can_transition(WorkOrderState.BUILDING, WorkOrderState.SUCCESS)
        assert can_transition(WorkOrderState.SUCCESS, WorkOrderState.CLOSED)

    def test_failure_path(self):
        """BUILDING can transition to FAILED, then retry or close."""
        assert can_transition(WorkOrderState.BUILDING, WorkOrderState.FAILED)
        assert can_transition(WorkOrderState.FAILED, WorkOrderState.BUILDING)  # retry
        assert can_transition(WorkOrderState.FAILED, WorkOrderState.CLOSED)

    def test_draft_can_be_closed(self):
        """DRAFT can be closed directly."""
        assert can_transition(WorkOrderState.DRAFT, WorkOrderState.CLOSED)

    def test_pending_approval_can_be_closed(self):
        """Approval can be rejected (closed)."""
        assert can_transition(WorkOrderState.PENDING_APPROVAL, WorkOrderState.CLOSED)

    def test_invalid_transitions_rejected(self):
        """Invalid transitions are not allowed."""
        assert not can_transition(WorkOrderState.DRAFT, WorkOrderState.SUCCESS)
        assert not can_transition(WorkOrderState.CLOSED, WorkOrderState.DRAFT)
        assert not can_transition(WorkOrderState.SUCCESS, WorkOrderState.FAILED)
        assert not can_transition(WorkOrderState.PENDING_APPROVAL, WorkOrderState.BUILDING)

    def test_transition_function_raises_on_invalid(self):
        """transition() raises StateError on invalid transition."""
        with pytest.raises(StateError, match="Invalid transition"):
            transition(WorkOrderState.CLOSED, WorkOrderState.DRAFT)

    def test_transition_function_returns_new_state(self):
        """transition() returns the target state on success."""
        result = transition(WorkOrderState.DRAFT, WorkOrderState.PENDING_APPROVAL)
        assert result == WorkOrderState.PENDING_APPROVAL

    def test_terminal_states(self):
        """CLOSED is terminal."""
        assert is_terminal(WorkOrderState.CLOSED)
        assert not is_terminal(WorkOrderState.DRAFT)
        assert not is_terminal(WorkOrderState.BUILDING)

    def test_secondary_review_flow(self):
        """Sensitive file change → secondary review."""
        assert can_transition(WorkOrderState.APPROVED, WorkOrderState.PENDING_SECOND_REVIEW)
        assert can_transition(WorkOrderState.PENDING_SECOND_REVIEW, WorkOrderState.SECOND_APPROVED)
        assert can_transition(WorkOrderState.PENDING_SECOND_REVIEW, WorkOrderState.SECOND_REJECTED)
        assert can_transition(WorkOrderState.SECOND_APPROVED, WorkOrderState.BUILDING)
        assert can_transition(WorkOrderState.SECOND_REJECTED, WorkOrderState.CLOSED)

    def test_all_transitions_defined(self):
        """Every state has transitions defined."""
        for state in WorkOrderState:
            assert state in TRANSITIONS, f"{state} missing from TRANSITIONS"

    def test_no_self_transitions(self):
        """No state can transition to itself."""
        for state, targets in TRANSITIONS.items():
            assert state not in targets, f"{state} has self-transition"
