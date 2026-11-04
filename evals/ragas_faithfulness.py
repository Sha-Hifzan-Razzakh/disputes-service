"""LLM faithfulness of the drafter's rationale to the evidence it cites (Ragas). Optional and key-gated.

The rules drafter quotes evidence verbatim, so this only means something with APP_MODEL_PROVIDER=anthropic.

    uv pip install ragas            # not a project dependency: only this script needs it
    ANTHROPIC_API_KEY=... uv run python -m evals.ragas_faithfulness [--limit 40] [--min 0.85]

Drafts each promptfoo case with the production LLM drafter, then scores rationale vs. the cited excerpts.
Exit 1 when the mean is below --min.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--min", type=float, default=0.85)
    a = ap.parse_args()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set; skipping faithfulness")
        return 0
    try:
        from langchain_anthropic import ChatAnthropic
        from ragas import SingleTurnSample
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import Faithfulness
    except ImportError:
        print("ragas not installed (uv pip install ragas); skipping faithfulness")
        return 0
    from evals.promptfoo.cases import generate_tests

    llm = ChatAnthropic(model=os.environ.get("APP_MODEL_NAME", "claude-sonnet-4-5"), temperature=0)  # type: ignore[call-arg]
    metric = Faithfulness(llm=LangchainLLMWrapper(llm))
    scores: list[float] = []
    for t in generate_tests()[: a.limit]:
        v = t["vars"]
        reply = await llm.ainvoke(v["prompt"])
        text = reply.content if isinstance(reply.content, str) else "".join(map(str, reply.content))
        try:
            draft = json.loads(text.strip().removeprefix("```json").removesuffix("```"))
        except ValueError:
            print(f"{v['case_id']}: unparseable reply, scored 0")
            scores.append(0.0)
            continue
        excerpts = [
            line
            for line in v["prompt"].splitlines()
            if any(line.startswith(f"[{c}]") for c in draft.get("citations", []))
        ]
        sample = SingleTurnSample(
            user_input=v["prompt"], response=draft.get("rationale", ""), retrieved_contexts=excerpts or [""]
        )
        s = float(await metric.single_turn_ascore(sample))
        scores.append(s)
        print(f"{v['case_id']}: {s:.2f}")
    mean = statistics.fmean(scores) if scores else 0.0
    print(f"faithfulness mean {mean:.3f} over {len(scores)} cases (min {a.min})")
    return 0 if mean >= a.min else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
