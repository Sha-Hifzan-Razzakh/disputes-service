"""Application-level conflicts the API maps to status codes (see app/api/errors.py)."""

from __future__ import annotations


class ResolutionInProgress(Exception):
    def __init__(self, dispute_id: str, thread_id: str | None = None) -> None:
        super().__init__(f"a resolution for dispute {dispute_id} is already in progress ({thread_id or 'running'})")
        self.thread_id = thread_id


class ApprovalDecided(Exception):
    pass


class ApprovalExpired(Exception):
    pass


class RunAborted(Exception):
    """A graph run stopped for an internal reason (e.g. recursion limit): a bug, not a business outcome."""

    def __init__(self, thread_id: str, reason: str) -> None:
        super().__init__(f"run {thread_id} aborted: {reason}")
        self.thread_id = thread_id
        self.reason = reason


def is_retryable(exc: BaseException) -> bool:
    """Error taxonomy (docs/errors.md): only TransientError is retried automatically. Business outcomes are state,
    not exceptions; domain and authorization errors are final; everything else is a bug and surfaces as 5xx."""
    from app.application.ports import TransientError

    return isinstance(exc, TransientError)


class Forbidden(Exception):
    """Authenticated but not allowed (role rank, four-eyes)."""


class ApprovalChanged(Exception):
    """The approver decided on a summary that differs from the stored one (stale screen or tampered payload)."""


class ThreadBusy(Exception):
    """Another worker is running this thread right now (advisory lock held)."""


class SideEffectUnsettled(Exception):
    """A refund or representment for this dispute may already have happened (pending, unknown or succeeded without
    the dispute being resolved). A new resolution would get a new idempotency key and could pay twice, so it waits
    for reconciliation or an operator."""

    def __init__(self, dispute_id: str, keys: list[str]) -> None:
        super().__init__(f"dispute {dispute_id} has an unsettled side effect ({len(keys)}); reconcile it first")
        self.keys = keys


class FeatureDisabled(Exception):
    """The rollout flag does not cover this tenant (APP_RESOLUTION_TENANTS)."""


class TooManyRuns(Exception):
    """Admission control: the process is at its concurrent-run limit."""
