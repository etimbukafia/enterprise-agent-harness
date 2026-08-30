"""General outcome verification against returned tool evidence."""

from __future__ import annotations

from ..contracts import OutcomeProposal, ToolResult, VerificationResult


def verify_outcome(
    *,
    proposal: OutcomeProposal,
    tool_results: list[ToolResult],
) -> VerificationResult:
    """Check that provider references belong to returned tool data.

    The verifier does not require every workflow to use evidence. When a
    workflow uses evidence, every reference must come from a tool result.
    """

    returned_ids = {item.evidence_id for result in tool_results for item in result.evidence}
    requested_ids = set(proposal.evidence_ids)
    invalid = sorted(requested_ids - returned_ids)
    coverage = (
        len(requested_ids.intersection(returned_ids)) / len(requested_ids) if requested_ids else 1.0
    )
    return VerificationResult(
        supported=not invalid,
        confidence=proposal.confidence,
        evidence_coverage=coverage,
        invalid_evidence_ids=invalid,
        reasons=["provider referenced unavailable evidence"] if invalid else [],
    )
