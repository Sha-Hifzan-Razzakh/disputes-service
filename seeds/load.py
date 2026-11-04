"""Load seed data into a migrated database. Idempotent per tenant (skips tenants that exist)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.dispute import Dispute, DisputeStatus
from app.domain.money import Money
from app.domain.reason_codes import lookup
from app.infrastructure.db.models import TenantRow, TransactionRow
from app.infrastructure.repositories import SqlDisputeRepository
from app.infrastructure.retrieval import HashingEmbedder, SqlPolicyStore
from seeds import data

STATUS_PATH = {
    "OPENED": [],
    "EVIDENCE_GATHERING": ["start_evidence"],
    "UNDER_REVIEW": ["start_evidence", "submit_for_review"],
}


async def load(sessions: async_sessionmaker[AsyncSession], *, now: datetime | None = None) -> dict[str, list[str]]:
    now = now or datetime.now(UTC)
    store = SqlPolicyStore(sessions, HashingEmbedder())
    created: dict[str, list[str]] = {}
    async with sessions.begin() as s:
        existing = set((await s.scalars(select(TenantRow.id))).all())
        new_tenants = [t for t in data.TENANTS if t["id"] not in existing]
        for t in new_tenants:
            s.add(TenantRow(**t))
        await s.flush()
        for tid, ref, amt, card, avs, cvv, tds, dstat, track, refunded, desc, days in data.TRANSACTIONS:
            if tid in existing:
                continue
            cur = next(t["currency"] for t in data.TENANTS if t["id"] == tid)
            s.add(
                TransactionRow(
                    tenant_id=tid,
                    ref=ref,
                    amount=data.money(amt),
                    currency=cur,
                    captured_at=now - timedelta(days=days),
                    card_brand=card,
                    avs_result=avs,
                    cvv_result=cvv,
                    three_ds_authenticated=tds,
                    delivery_status=dstat,
                    delivery_tracking=track,
                    refunded_amount=data.money(refunded),
                    description=desc,
                )
            )
        await s.flush()
        repo = SqlDisputeRepository(s)
        for tid, ref, net, code, amt, status in data.DISPUTES:
            if tid in existing:
                continue
            cur = next(t["currency"] for t in data.TENANTS if t["id"] == tid)
            txn_amt = next(x[2] for x in data.TRANSACTIONS if x[1] == ref)
            d = Dispute.open(
                tenant_id=tid,
                reason=lookup(net, code),
                transaction_ref=ref,
                amount=Money.of(amt, cur),
                transaction_amount=Money.of(txn_amt, cur),
                actor="seed",
                now=now - timedelta(days=2),
            )
            for action in STATUS_PATH[status]:
                d.apply(action, actor="seed", now=now - timedelta(days=1))
            assert d.status == DisputeStatus(status)
            await repo.add(d)
            created.setdefault(tid, []).append(str(d.id))
    if not existing:
        for title, source, text in data.NETWORK_DOCS:
            await store.add_document(tenant_id=None, title=title, source=source, text=text)
    for t in data.TENANTS:
        if t["id"] in existing:
            continue
        for title, source, text in data.TENANT_DOCS[t["id"]]:
            await store.add_document(tenant_id=t["id"], title=title, source=source, text=text)
    return created


def main() -> None:
    from app.bootstrap.settings import get_settings
    from app.infrastructure.db.session import make_engine, make_sessionmaker

    async def run() -> None:
        engine = make_engine(get_settings().database_url.get_secret_value())
        try:
            created = await load(make_sessionmaker(engine))
            print({k: len(v) for k, v in created.items()} or "already seeded")
        finally:
            await engine.dispose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
