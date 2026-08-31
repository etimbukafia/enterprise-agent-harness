"""Event-driven and background execution contracts and deterministic stores."""

from .deduplication import (
    DeduplicationConflictError,
    DeduplicationStore,
    DedupRecord,
    DedupStatus,
    InMemoryDeduplicationStore,
)
from .events import EventDisposition, EventEnvelope, EventTrigger, FailureCategory
from .leases import (
    InMemoryLeaseStore,
    Lease,
    LeaseConflictError,
    LeaseExpiredError,
    LeaseStore,
)
from .runner import (
    BackgroundJobRunner,
    BackgroundRetryPolicy,
    DeadLetterRecord,
    DeadLetterSink,
    JobHandler,
    JobResult,
    ListDeadLetterSink,
)

__all__ = [
    "BackgroundJobRunner",
    "BackgroundRetryPolicy",
    "DeadLetterRecord",
    "DeadLetterSink",
    "DedupRecord",
    "DedupStatus",
    "DeduplicationConflictError",
    "DeduplicationStore",
    "EventDisposition",
    "EventEnvelope",
    "EventTrigger",
    "FailureCategory",
    "InMemoryDeduplicationStore",
    "InMemoryLeaseStore",
    "JobHandler",
    "JobResult",
    "Lease",
    "LeaseConflictError",
    "LeaseExpiredError",
    "LeaseStore",
    "ListDeadLetterSink",
]
