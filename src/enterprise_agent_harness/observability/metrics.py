"""Deterministic cost model and usage aggregation for executions."""

from __future__ import annotations

from typing import Protocol

from ..contracts import (
    ExecutionMetrics,
    ProviderUsageMetric,
    ToolExecutionRecord,
    ToolUsageMetric,
)
from ..providers.contracts import ProviderCallRecord


class CostModel(Protocol):
    """Boundary that prices provider usage.

    The runtime does not hard-code unstable provider pricing. When no model is
    configured, cost is zero. A consumer may inject a richer pricing source.
    """

    def cost(
        self,
        *,
        provider_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """Return the estimated monetary cost for one provider operation."""


class StaticTokenCostModel:
    """Constant per-1k-token pricing with a zero-cost deterministic default."""

    def __init__(
        self,
        *,
        input_cost_per_1k: float = 0.0,
        output_cost_per_1k: float = 0.0,
    ) -> None:
        if input_cost_per_1k < 0 or output_cost_per_1k < 0:
            raise ValueError("cost rates must not be negative")
        self.input_cost_per_1k = input_cost_per_1k
        self.output_cost_per_1k = output_cost_per_1k

    def cost(
        self,
        *,
        provider_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        del provider_id, model
        input_cost = (input_tokens / 1000.0) * self.input_cost_per_1k
        output_cost = (output_tokens / 1000.0) * self.output_cost_per_1k
        return round(input_cost + output_cost, 8)


def aggregate_metrics(
    *,
    execution_id: str,
    correlation_id: str,
    agent_id: str,
    agent_version: str,
    attempt: int,
    execution_latency_ms: float,
    provider_calls: list[ProviderCallRecord],
    tool_executions: list[ToolExecutionRecord],
    cost_model: CostModel | None,
) -> ExecutionMetrics:
    """Build attributable usage and cost evidence from recorded calls."""

    provider_metrics: dict[tuple[str, str, str], ProviderUsageMetric] = {}
    tool_metrics: dict[tuple[str, str], ToolUsageMetric] = {}
    total_input_tokens = 0
    total_output_tokens = 0
    total_cost = 0.0
    provider_latency_ms = 0.0

    for record in provider_calls:
        metadata = record.metadata
        input_tokens = metadata.input_tokens or 0
        output_tokens = metadata.output_tokens or 0
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens
        provider_latency_ms += metadata.latency_ms
        cost = 0.0
        if cost_model is not None:
            cost = cost_model.cost(
                provider_id=metadata.provider_id,
                model=metadata.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        total_cost += cost
        key = (metadata.provider_id, metadata.provider_version, metadata.model)
        metric = provider_metrics.get(key)
        if metric is None:
            metric = ProviderUsageMetric(
                provider_id=metadata.provider_id,
                provider_version=metadata.provider_version,
                model=metadata.model,
            )
            provider_metrics[key] = metric
        metric = metric.model_copy(
            update={
                "calls": metric.calls + 1,
                "input_tokens": metric.input_tokens + input_tokens,
                "output_tokens": metric.output_tokens + output_tokens,
                "total_tokens": metric.total_tokens + input_tokens + output_tokens,
                "latency_ms": metric.latency_ms + metadata.latency_ms,
                "cost": round(metric.cost + cost, 8),
            }
        )
        provider_metrics[key] = metric

    tool_latency_ms = 0.0
    for tool_record in tool_executions:
        tool_latency_ms += tool_record.latency_ms
        tool_key = (tool_record.tool_id, tool_record.tool_version)
        tool_metric = tool_metrics.get(tool_key)
        if tool_metric is None:
            tool_metric = ToolUsageMetric(
                tool_id=tool_record.tool_id,
                tool_version=tool_record.tool_version,
            )
            tool_metrics[tool_key] = tool_metric
        tool_metric = tool_metric.model_copy(
            update={
                "invocations": tool_metric.invocations + 1,
                "latency_ms": tool_metric.latency_ms + tool_record.latency_ms,
            }
        )
        tool_metrics[tool_key] = tool_metric

    return ExecutionMetrics(
        execution_id=execution_id,
        correlation_id=correlation_id,
        agent_id=agent_id,
        agent_version=agent_version,
        attempt=attempt,
        execution_latency_ms=execution_latency_ms,
        provider_calls=len(provider_calls),
        provider_latency_ms=provider_latency_ms,
        tool_invocations=len(tool_executions),
        tool_latency_ms=tool_latency_ms,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        total_tokens=total_input_tokens + total_output_tokens,
        total_cost=round(total_cost, 8),
        providers=tuple(provider_metrics.values()),
        tools=tuple(tool_metrics.values()),
    )
