"""Background job (T11.8 + reconciliation): expire approvals past their TTL and resume their threads with the
stored decision; settle side effects left `unknown`/`pending` by looking them up by idempotency key.

Runs in-process every APP_SWEEPER_INTERVAL_S, or once from `scripts/sweep.py` under an external scheduler. Several
replicas may sweep at once: the approvals CAS lets exactly one of them expire each approval, the thread lock lets one
resume it, and reconciliation is a read plus an idempotent update."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import structlog
from opentelemetry import metrics
from opentelemetry.metrics import Observation

from app.application.approvals import SWEEPER, ApprovalService, resume_value

if TYPE_CHECKING:
    from app.bootstrap.container import Container

log = structlog.get_logger()
_thread_counts: dict[str, int] = {}


def _observe_threads(_: Any) -> Iterable[Observation]:
    return [Observation(n, {"status": status}) for status, n in _thread_counts.items()]


metrics.get_meter("disputes.ops").create_observable_gauge(
    "resolution_threads",
    callbacks=[_observe_threads],
    description="Resolution threads by status (refreshed each sweep)",
)
LOOKUP = {"refund": "payments.get_refund", "representment": "network.get_submission"}


class Sweeper:
    def __init__(self, c: Container) -> None:
        self.c = c

    def _ctx(self, tenant_id: str) -> Any:
        return self.c.graph_context(
            tenant_id=tenant_id, user_id=SWEEPER, role="admin", request_id=f"sweep-{uuid.uuid4().hex[:12]}"
        )

    async def run_once(self) -> dict[str, int]:
        gs = self.c.graph_services
        svc = ApprovalService(gs.approvals, gs.audit, gs.clock)
        runner = self.c.resolution_runner()
        expired = await svc.expire_due()
        resumed = 0
        for rec in expired:
            try:
                await runner.resume(rec.thread_id, resume_value(rec), self._ctx(rec.tenant_id))
                resumed += 1
            except Exception:
                log.exception("sweeper.resume_failed", thread_id=rec.thread_id)
                # only a thread still waiting for this decision is marked; a run in progress keeps its status
                await gs.threads.set_status(
                    rec.thread_id, "resume_failed", only_from=("awaiting_approval", "running", "failed")
                )
        reconciled = await self.reconcile()
        counts = await gs.threads.counts("resolution")  # ResolutionsStuck alert: failed / resume_failed > 0
        _thread_counts.clear()
        _thread_counts.update(
            {k: counts.get(k, 0) for k in ("running", "awaiting_approval", "resume_failed", "failed")}
        )
        return {"expired": len(expired), "resumed": resumed, "reconciled": reconciled}

    async def reconcile(self) -> int:
        gs = self.c.graph_services
        cutoff = gs.clock.now() - timedelta(minutes=self.c.settings.reconcile_after_minutes)
        settled = 0
        for se in await gs.side_effects.stale_unknown(cutoff, limit=100):
            res = await gs.tools.call(LOOKUP[se.kind], {"idempotency_key": se.idempotency_key}, tenant_id=se.tenant_id)
            if not res.ok:
                continue  # lookup unavailable; try next sweep (alert fires on age, stage 25)
            found = bool(res.data.get("found"))
            ref = str(res.data.get("external_ref")) if found else None
            await gs.side_effects.finish(
                se.idempotency_key,
                status="succeeded" if found else "failed",
                external_ref=ref,
                error=None if found else "not found at provider",
            )
            await gs.audit.record(
                tenant_id=se.tenant_id,
                actor=SWEEPER,
                action="side_effect.reconciled",
                subject=f"dispute:{se.dispute_id}",
                request_id="sweeper",
                data={"key": se.idempotency_key, "found": found, "external_ref": ref},
                dedupe_key=f"side_effect.reconciled:{se.idempotency_key}",
            )
            if found:  # money moved or representment filed although the run ended EXECUTION_FAILED: humans decide
                await gs.outbox.enqueue(
                    tenant_id=se.tenant_id,
                    type="dispute.side_effect_reconciled",
                    payload={"dispute_id": str(se.dispute_id), "kind": se.kind, "external_ref": ref},
                    dedupe_key=f"reconciled:{se.idempotency_key}",
                )
            settled += 1
        return settled

    async def loop(self, interval_s: float) -> None:
        while True:
            try:
                counts = await self.run_once()
                if any(counts.values()):
                    log.info("sweeper.run", **counts)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("sweeper.error")
            await asyncio.sleep(interval_s)
