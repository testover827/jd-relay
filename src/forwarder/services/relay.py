"""Relay Service — core business logic tying state machine + DingTalk + Agent.

Coordinates the full work order lifecycle:
1. Work order created (DRAFT)
2. Submit for approval → PENDING_APPROVAL
3. Three-person approval → APPROVED / CLOSED
4. Trigger Jenkins build via Agent → BUILDING
5. Build result → SUCCESS / FAILED → CLOSED

Secondary review flow (sensitive file change):
6. special.md change detected → PENDING_SECOND_REVIEW → SECOND_APPROVED/REJECTED
"""

import json
import logging
from typing import Optional

from ..state import WorkOrderState, transition, StateError
from ..crypto.envelope import MessageType

logger = logging.getLogger(__name__)


class RelayError(Exception):
    """Business logic error."""
    pass


class RelayService:
    """Core relay business logic.

    Coordinates between:
    - DingTalk (approval creation, notifications)
    - Agent via WebSocket (build triggers)
    - Database (work order persistence)
    - State machine (lifecycle enforcement)
    """

    def __init__(
        self,
        dingtalk_service=None,
        db_session_factory=None,
        forwarder_server=None,
    ):
        self._dt = dingtalk_service
        self._db_factory = db_session_factory
        self._fwd = forwarder_server

    # ── Work Order Lifecycle ───────────────────────────────────

    async def submit_for_approval(
        self,
        work_order: dict,
        originator_user_id: str,
        process_code: str,
        approvers: list[str],
    ) -> str:
        """Submit a DRAFT work order for three-person approval.

        1. Validate current state is DRAFT
        2. Create DingTalk approval instance
        3. Transition to PENDING_APPROVAL
        4. Return approval instance_id

        Args:
            work_order: Work order dict (must have 'state' and 'id').
            originator_user_id: Initiator's DingTalk userId.
            process_code: Approval template processCode.
            approvers: List of 3 approver userIds.

        Returns:
            DingTalk process instance ID.
        """
        current = WorkOrderState(work_order["state"])
        transition(current, WorkOrderState.PENDING_APPROVAL)

        if not self._dt:
            raise RelayError("DingTalk service not configured")

        # Create DingTalk approval
        form_values = [
            {"name": "工单编号", "value": work_order.get("order_no", "")},
            {"name": "ISSUE", "value": work_order.get("issue", "")},
            {"name": "项目", "value": work_order.get("project", "")},
            {"name": "分支", "value": work_order.get("branch", "")},
            {"name": "构建命令", "value": work_order.get("build_cmd", "")},
        ]

        title = f"[JD-Relay] {work_order.get('issue')} — {work_order.get('project')}"

        instance_id = await self._dt.create_approval(
            originator_user_id=originator_user_id,
            process_code=process_code,
            title=title,
            form_values=form_values,
            approvers=approvers,
        )

        logger.info(
            f"Work order {work_order['order_no']} submitted for approval: {instance_id}"
        )
        return instance_id

    async def on_approval_result(
        self,
        work_order: dict,
        result: str,  # "agree" or "refuse"
        approver: str,
        comment: str = "",
    ) -> WorkOrderState:
        """Handle an approval callback result.

        1. Record approval in database
        2. If agreed: transition APPROVED → check if three approvers have agreed
        3. If refused: transition CLOSED

        Returns the new state.
        """
        current = WorkOrderState(work_order["state"])

        if result == "agree":
            new_state = transition(current, WorkOrderState.APPROVED)
            logger.info(f"Work order {work_order.get('order_no')} approved by {approver}")
            return new_state
        else:
            new_state = transition(current, WorkOrderState.CLOSED)
            logger.info(f"Work order {work_order.get('order_no')} rejected by {approver}")
            # Notify submitter
            return new_state

    async def trigger_build(self, work_order: dict) -> bool:
        """Send BUILD_TRIGGER to the Agent responsible for this project.

        1. Validate state is APPROVED (or SECOND_APPROVED)
        2. Transition to BUILDING
        3. Send BUILD_TRIGGER via Agent WebSocket

        Returns True if build was triggered successfully.
        """
        current = WorkOrderState(work_order["state"])
        transition(current, WorkOrderState.BUILDING)

        project = work_order.get("project", "")
        payload = json.dumps({
            "work_order_id": work_order["order_no"],
            "issue": work_order["issue"],
            "project": project,
            "branch": work_order["branch"],
            "build_cmd": work_order.get("build_cmd", ""),
        })

        if not self._fwd:
            raise RelayError("Forwarder server not configured")

        success = await self._fwd.send_to_project(
            project, MessageType.BUILD_TRIGGER, payload
        )

        if not success:
            # Build trigger failed — revert to APPROVED so user can retry
            logger.error(f"No agent for project '{project}', build trigger failed")
            raise RelayError(f"No agent online for project '{project}'")

        logger.info(f"Build triggered for {work_order['order_no']} → {project}")
        return True

    async def on_build_result(
        self,
        work_order: dict,
        build_status: str,
        build_number: int | None = None,
        log_url: str = "",
        duration: int | None = None,
    ) -> WorkOrderState:
        """Handle a BUILD_RESULT message from an Agent.

        1. Validate state is BUILDING
        2. Record build result
        3. Transition to SUCCESS or FAILED
        4. Send notification via DingTalk

        Returns the new state.
        """
        current = WorkOrderState(work_order["state"])

        if build_status.upper() == "SUCCESS":
            new_state = transition(current, WorkOrderState.SUCCESS)
        elif build_status.upper() == "FAILED":
            new_state = transition(current, WorkOrderState.FAILED)
        else:
            raise RelayError(f"Unknown build status: {build_status}")

        logger.info(
            f"Build result for {work_order['order_no']}: "
            f"#{build_number} → {build_status}"
        )
        return new_state

    async def close_work_order(self, work_order: dict) -> WorkOrderState:
        """Close a work order (terminal state)."""
        current = WorkOrderState(work_order["state"])
        new_state = transition(current, WorkOrderState.CLOSED)
        logger.info(f"Work order {work_order['order_no']} closed")
        return new_state

    # ── Secondary Review (Sensitive File) ──────────────────────

    async def on_sensitive_change_detected(
        self,
        work_order: dict,
        changed_files: list[str],
        approvers: list[str],
    ) -> str:
        """Handle a SENSITIVE_REVIEW_REQ from an Agent.

        1. Validate state is APPROVED
        2. Transition to PENDING_SECOND_REVIEW
        3. Create new DingTalk approval for secondary review
        4. Return second approval instance_id
        """
        current = WorkOrderState(work_order["state"])
        transition(current, WorkOrderState.PENDING_SECOND_REVIEW)

        if not self._dt:
            raise RelayError("DingTalk service not configured")

        form_values = [
            {"name": "工单编号", "value": work_order.get("order_no", "")},
            {"name": "变更文件", "value": ", ".join(changed_files)},
            {"name": "风险说明", "value": "special.md 文件变更，需要二次审核"},
        ]

        title = f"[二次审核] {work_order.get('issue')} — 敏感文件变更"

        instance_id = await self._dt.create_approval(
            originator_user_id=work_order.get("created_by", ""),
            process_code="SECOND_REVIEW",  # Configure in DingTalk admin
            title=title,
            form_values=form_values,
            approvers=approvers,
        )

        logger.info(
            f"Secondary review created for {work_order['order_no']}: {instance_id}"
        )
        return instance_id

    async def on_second_review_result(
        self,
        work_order: dict,
        result: str,
        approver: str,
    ) -> WorkOrderState:
        """Handle secondary review result.

        - SECOND_APPROVED → can proceed to BUILDING
        - SECOND_REJECTED → CLOSED
        """
        current = WorkOrderState(work_order["state"])

        if result == "agree":
            new_state = transition(current, WorkOrderState.SECOND_APPROVED)
            logger.info(f"Second review approved for {work_order['order_no']}")
            return new_state
        else:
            new_state = transition(current, WorkOrderState.SECOND_REJECTED)
            logger.info(f"Second review rejected for {work_order['order_no']}")
            return new_state
