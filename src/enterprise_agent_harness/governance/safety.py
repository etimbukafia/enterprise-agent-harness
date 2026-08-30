"""Deterministic injection detection and outcome safety decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..contracts import (
    AgentPlan,
    OutcomeProposal,
    OutcomeStatus,
    RiskLevel,
    RuntimeConfig,
    SafetyFlag,
    ToolResult,
    ToolResultStatus,
    VerificationResult,
)

DIRECT_INJECTION_PATTERNS = (
    re.compile(
        r"\bignore\s+(?:all|any|the|your|previous|prior)\s+(?:system\s+)?(?:instructions|rules|policy)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:reveal|show|print|dump)\b.{0,50}\b(?:system|developer)\s+prompt\b", re.IGNORECASE
    ),
    re.compile(
        r"\b(?:change|bypass|override)\b.{0,50}\b(?:permission|role|access|policy)\b", re.IGNORECASE
    ),
    re.compile(
        r"\b(?:you are now|pretend you are|act as)\b.{0,50}\b(?:admin|system|developer)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bdo not follow\b.{0,50}\b(?:policy|instructions|rules)\b", re.IGNORECASE),
)

INDIRECT_INJECTION_PATTERNS = (
    re.compile(
        r"\bignore\s+(?:all|any|the|your|previous|prior)\s+(?:system\s+)?(?:instructions|rules|policy)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:system|developer)\s+(?:message|instruction|prompt)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:reveal|exfiltrate|send|forward)\b.{0,60}\b(?:secret|prompt|credential|token|private)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:change|bypass|override)\b.{0,50}\b(?:permission|role|access|policy)\b", re.IGNORECASE
    ),
)


def direct_injection_matches(text: str) -> list[str]:
    """Return deterministic pattern identifiers for direct injection text."""

    return [pattern.pattern for pattern in DIRECT_INJECTION_PATTERNS if pattern.search(text)]


def indirect_injection_matches(text: str) -> list[str]:
    """Return deterministic pattern identifiers for untrusted output text."""

    return [pattern.pattern for pattern in INDIRECT_INJECTION_PATTERNS if pattern.search(text)]


@dataclass(frozen=True)
class SafetyDecision:
    """Runtime decision derived from typed tool results and verification."""

    status: OutcomeStatus
    flags: tuple[SafetyFlag, ...] = ()
    reasons: tuple[str, ...] = ()
    human_review_required: bool = False
    escalation_code: str | None = None


class SafetyPolicy:
    """Apply deterministic safety rules after tool execution."""

    def __init__(self, config: RuntimeConfig | None = None) -> None:
        self.config = config or RuntimeConfig()

    def inspect_input(self, input_text: str) -> SafetyDecision | None:
        """Return a refusal when input contains a direct injection pattern."""

        if direct_injection_matches(input_text):
            return SafetyDecision(
                status=OutcomeStatus.REFUSED,
                flags=(SafetyFlag.DIRECT_PROMPT_INJECTION,),
                reasons=("input attempted to override runtime instructions or authority",),
                escalation_code="direct_prompt_injection",
            )
        return None

    def decide(
        self,
        *,
        tool_results: list[ToolResult],
        proposal: OutcomeProposal,
        verification: VerificationResult,
        highest_risk: RiskLevel = RiskLevel.LOW,
        plan: AgentPlan | None = None,
    ) -> SafetyDecision:
        """Choose the final outcome state without consulting provider policy."""

        del plan
        flags: list[SafetyFlag] = []
        reasons: list[str] = []
        restricted = any(
            result.restricted or result.status == ToolResultStatus.RESTRICTED
            for result in tool_results
        )
        if restricted:
            return SafetyDecision(
                status=OutcomeStatus.REFUSED,
                flags=(SafetyFlag.RESTRICTED_RESULT,),
                reasons=("a tool returned data that is not available to this principal",),
                escalation_code="restricted_result",
            )

        if verification.invalid_evidence_ids:
            flags.append(SafetyFlag.VERIFICATION_FAILED)
            reasons.append("the provider referenced data that no tool returned")
            if highest_risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
                return SafetyDecision(
                    status=OutcomeStatus.ESCALATED,
                    flags=tuple(flags),
                    reasons=tuple(reasons),
                    human_review_required=True,
                    escalation_code="outcome_verification_failed",
                )
            return SafetyDecision(
                status=OutcomeStatus.NEEDS_INPUT,
                flags=tuple(flags),
                reasons=tuple(reasons),
                human_review_required=True,
                escalation_code="outcome_verification_failed",
            )

        injections = any(result.injection_flags for result in tool_results)
        conflicts = any(result.conflicts for result in tool_results)
        failures = [
            result
            for result in tool_results
            if result.status in {ToolResultStatus.FAILED, ToolResultStatus.INVALID_ARGUMENTS}
        ]
        successes = [
            result for result in tool_results if result.status == ToolResultStatus.SUCCEEDED
        ]

        if not tool_results:
            return SafetyDecision(
                status=OutcomeStatus.NEEDS_INPUT,
                flags=(SafetyFlag.NO_RESULT,),
                reasons=("the plan returned no tool result",),
            )
        if not successes and not failures:
            return SafetyDecision(
                status=OutcomeStatus.NEEDS_INPUT,
                flags=(SafetyFlag.NO_RESULT,),
                reasons=("the tools returned no usable result",),
            )
        if not successes and failures:
            return SafetyDecision(
                status=OutcomeStatus.FAILED,
                flags=(SafetyFlag.TOOL_FAILURE,),
                reasons=("all proposed tool calls failed",),
                escalation_code="all_tools_failed",
            )

        if failures:
            flags.append(SafetyFlag.TOOL_FAILURE)
            reasons.append("one or more tool calls failed")
        if conflicts:
            flags.append(SafetyFlag.CONFLICTING_RESULT)
            reasons.append("tool results contain conflicting claims")
        if injections:
            flags.append(SafetyFlag.INDIRECT_PROMPT_INJECTION)
            reasons.append(
                "tool output contained instruction-like text and remained untrusted data"
            )

        if highest_risk in {RiskLevel.HIGH, RiskLevel.CRITICAL} and (conflicts or injections):
            return SafetyDecision(
                status=OutcomeStatus.ESCALATED,
                flags=tuple(flags),
                reasons=tuple(reasons),
                human_review_required=True,
                escalation_code="high_risk_unresolved_result",
            )
        if conflicts or injections or failures:
            return SafetyDecision(
                status=OutcomeStatus.PARTIAL,
                flags=tuple(flags),
                reasons=tuple(reasons),
                human_review_required=True,
                escalation_code="result_requires_review",
            )

        if proposal.confidence < self.config.minimum_confidence_threshold:
            flags.append(SafetyFlag.LOW_CONFIDENCE)
            reasons.append("provider confidence is below the minimum threshold")
            if highest_risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
                return SafetyDecision(
                    status=OutcomeStatus.ESCALATED,
                    flags=tuple(flags),
                    reasons=tuple(reasons),
                    human_review_required=True,
                    escalation_code="low_confidence_high_risk",
                )
            return SafetyDecision(
                status=OutcomeStatus.NEEDS_INPUT,
                flags=tuple(flags),
                reasons=tuple(reasons),
            )
        if proposal.confidence < self.config.high_confidence_threshold:
            return SafetyDecision(
                status=OutcomeStatus.PARTIAL,
                flags=(SafetyFlag.LOW_CONFIDENCE,),
                reasons=("provider confidence is below the completed-outcome threshold",),
                human_review_required=True,
                escalation_code="moderate_confidence",
            )

        return SafetyDecision(status=OutcomeStatus.COMPLETED)
