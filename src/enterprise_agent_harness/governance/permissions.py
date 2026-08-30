"""Permission checks for provider-proposed tool calls."""

from __future__ import annotations

from typing import Any, Protocol

from ..contracts import ExecutionContext, PermissionDecision, PrincipalContext
from ..tools.definitions import ToolDefinition


class PermissionBroker(Protocol):
    """Application boundary that authorizes one proposed tool call."""

    def authorize(
        self,
        *,
        principal: PrincipalContext,
        execution: ExecutionContext,
        tool: ToolDefinition,
        arguments: dict[str, Any],
    ) -> PermissionDecision:
        """Return a decision before the handler can run."""


class DefaultPermissionBroker:
    """Deny-by-default broker based on the trusted execution context.

    The provider cannot add an authorized tool or an approval digest. A
    consuming application can replace this broker with resource policy checks.
    """

    def authorize(
        self,
        *,
        principal: PrincipalContext,
        execution: ExecutionContext,
        tool: ToolDefinition,
        arguments: dict[str, Any],
    ) -> PermissionDecision:
        if tool.tool_id not in execution.authorized_tool_ids:
            return PermissionDecision(
                allowed=False,
                principal_id=principal.principal_id,
                tenant_id=principal.tenant_id,
                tool_id=tool.tool_id,
                reason_code="tool_not_authorized",
            )

        missing_permissions = sorted(
            set(tool.required_permissions).difference(execution.granted_permissions)
        )
        if missing_permissions:
            return PermissionDecision(
                allowed=False,
                principal_id=principal.principal_id,
                tenant_id=principal.tenant_id,
                tool_id=tool.tool_id,
                reason_code="required_permission_missing",
            )

        if tool.requires_approval:
            if tool.action_digest(arguments) in execution.approved_action_digests:
                return PermissionDecision(
                    allowed=True,
                    principal_id=principal.principal_id,
                    tenant_id=principal.tenant_id,
                    tool_id=tool.tool_id,
                    reason_code="allowed_by_exact_approval",
                )
            return PermissionDecision(
                allowed=False,
                principal_id=principal.principal_id,
                tenant_id=principal.tenant_id,
                tool_id=tool.tool_id,
                reason_code="approval_required",
                approval_required=True,
            )

        return PermissionDecision(
            allowed=True,
            principal_id=principal.principal_id,
            tenant_id=principal.tenant_id,
            tool_id=tool.tool_id,
            reason_code="allowed_by_execution_authority",
        )
