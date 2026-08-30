"""Typed tool definitions with input and output validation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from ..contracts import (
    ExecutionContext,
    RiskLevel,
    ToolDescriptor,
    ToolKind,
    ToolResult,
    ToolResultStatus,
)


class ToolInvocationError(RuntimeError):
    """Raised when a tool proposal cannot pass its typed boundary."""

    def __init__(self, message: str, *, code: str = "tool_invocation_failed") -> None:
        super().__init__(message)
        self.code = code


ToolHandler = Callable[[ExecutionContext, BaseModel], BaseModel | ToolResult]


@dataclass(frozen=True)
class ToolDefinition:
    """Application-owned tool with typed input and output schemas.

    The handler is called only after the runtime has completed permission and
    argument checks. Tool output is untrusted data.
    """

    tool_id: str
    version: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: ToolHandler
    kind: ToolKind = ToolKind.READ
    risk_level: RiskLevel = RiskLevel.LOW
    required_permissions: tuple[str, ...] = ()
    requires_approval: bool = False
    idempotency_required: bool = False
    sensitive_argument_fields: tuple[str, ...] = ()
    owner_id: str = "application"
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("tool_id", self.tool_id),
            ("version", self.version),
            ("description", self.description),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be empty")
        if len(self.required_permissions) != len(set(self.required_permissions)):
            raise ValueError("required_permissions must not contain duplicates")
        if len(self.sensitive_argument_fields) != len(set(self.sensitive_argument_fields)):
            raise ValueError("sensitive_argument_fields must not contain duplicates")
        if not self.owner_id.strip():
            raise ValueError("owner_id must not be empty")
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("tags must not contain duplicates")
        if not issubclass(self.input_model, BaseModel):
            raise TypeError("input_model must be a Pydantic model")
        if not issubclass(self.output_model, BaseModel):
            raise TypeError("output_model must be a Pydantic model")

    @property
    def descriptor(self) -> ToolDescriptor:
        """Return provider-facing metadata without the handler."""

        return ToolDescriptor(
            tool_id=self.tool_id,
            version=self.version,
            description=self.description,
            kind=self.kind,
            risk_level=self.risk_level,
            input_fields=list(self.input_model.model_fields),
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
            required_permissions=list(self.required_permissions),
            requires_approval=self.requires_approval,
            idempotency_required=self.idempotency_required,
            owner_id=self.owner_id,
            tags=list(self.tags),
        )

    def validate_arguments(self, arguments: dict[str, Any]) -> BaseModel:
        """Validate one proposal without calling the handler."""

        try:
            return self.input_model.model_validate(arguments)
        except ValidationError as exc:
            raise ToolInvocationError(
                f"invalid arguments for tool {self.tool_id}",
                code="invalid_arguments",
            ) from exc

    def invoke(self, context: ExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        """Validate and invoke the application-owned handler."""

        parsed_arguments = self.validate_arguments(arguments)
        try:
            returned = self.handler(context, parsed_arguments)
        except ToolInvocationError:
            raise
        except Exception as exc:  # The runtime turns handler failures into a safe result.
            raise ToolInvocationError(
                f"tool {self.tool_id} failed",
                code="handler_failed",
            ) from exc

        if isinstance(returned, ToolResult):
            self._validate_enveloped_result(returned)
            normalized_output = returned.output
            if returned.output is not None:
                normalized_output = self._validate_output(returned.output).model_dump(mode="json")
            return returned.model_copy(
                update={
                    "tool_version": self.version,
                    "execution_id": context.execution_id,
                    "output": normalized_output,
                }
            )

        output = self._validate_output(returned)
        return ToolResult(
            tool_id=self.tool_id,
            tool_version=self.version,
            execution_id=context.execution_id,
            status=ToolResultStatus.SUCCEEDED,
            output=output.model_dump(mode="json"),
        )

    def action_digest(self, arguments: dict[str, Any]) -> str:
        """Return a stable digest for exact approval of this proposal."""

        payload = {
            "tool_id": self.tool_id,
            "tool_version": self.version,
            "arguments": arguments,
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    def redact_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Redact configured sensitive fields from returned call records."""

        sensitive = set(self.sensitive_argument_fields)
        return {
            key: "[REDACTED]" if key in sensitive else value for key, value in arguments.items()
        }

    def _validate_output(self, output: Any) -> BaseModel:
        try:
            return self.output_model.model_validate(output)
        except ValidationError as exc:
            raise ToolInvocationError(
                f"tool {self.tool_id} returned invalid output",
                code="invalid_output",
            ) from exc

    def _validate_enveloped_result(self, result: ToolResult) -> None:
        if result.tool_id != self.tool_id:
            raise ToolInvocationError(
                f"tool {self.tool_id} returned result for {result.tool_id}",
                code="invalid_result_identity",
            )
