"""Recommendation drafters.

RuleBasedDrafter is deterministic and needs no API key (default, used in tests).
AnthropicDrafter wraps a chat model (Claude via langchain-anthropic) when APP_MODEL_PROVIDER=anthropic.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field

from app.application.ports import PolicyHit, Recommendation


class RuleBasedDrafter:
    async def draft(self, *, case: dict[str, Any], question: str, hits: list[PolicyHit]) -> Recommendation:
        family = case["reason_family"]
        txn = case["transaction"]
        evidence = set(case.get("evidence_kinds", []))
        if family == "FRAUD":
            strong = txn["three_ds_authenticated"] and txn["avs_result"] == "Y" and txn["cvv_result"] == "M"
            res, why = (
                ("CONTEST", "3-D Secure authenticated with matching AVS/CVV")
                if strong
                else ("ACCEPT", "no strong authentication evidence on the transaction")
            )
        elif family == "NOT_RECEIVED":
            delivered = txn["delivery_status"] == "DELIVERED" and txn["delivery_tracking"]
            res, why = (
                ("CONTEST", "carrier shows the order delivered") if delivered else ("ACCEPT", "no proof of delivery")
            )
        elif family == "NOT_AS_DESCRIBED":
            res, why = (
                ("PARTIAL", "product description is on file")
                if "product_description" in evidence
                else ("ACCEPT", "no product description evidence")
            )
        else:
            res, why = ("ACCEPT", "processing errors are normally refunded")
        cites = [h.chunk_id for h in hits[:2]]
        lead = hits[0].text[:160] if hits else "no policy text found"
        text = f'Recommend {res}: {why}. Relevant policy: "{lead}". Question answered: {question}'
        return Recommendation(text=text, suggested_resolution=res, citations=cites)


class _Draft(BaseModel):
    recommendation: str = Field(description="Two to four sentences for the analyst")
    suggested_resolution: str = Field(description="One of ACCEPT, PARTIAL, CONTEST, REQUEST_INFO")
    cited_chunk_ids: list[str] = Field(default_factory=list)


class AnthropicDrafter:  # pragma: no cover - requires an API key
    """Adapter over a chat model built once in bootstrap (timeout, retries and rate limiter set there)."""

    def __init__(self, chat_model: BaseChatModel) -> None:
        self.llm = chat_model.with_structured_output(_Draft)

    async def draft(self, *, case: dict[str, Any], question: str, hits: list[PolicyHit]) -> Recommendation:
        ctx = "\n".join(f"[{h.chunk_id}] ({h.document_title}) {h.text}" for h in hits)
        prompt = (
            f"You help a merchant analyst handle a card dispute.\nCase: {case}\nPolicy excerpts:\n{ctx}\n"
            f"Question: {question}\nRecommend a resolution and cite chunk ids."
        )
        out: _Draft = await self.llm.ainvoke(prompt)  # type: ignore[assignment]
        return Recommendation(
            text=out.recommendation, suggested_resolution=out.suggested_resolution, citations=out.cited_chunk_ids
        )
