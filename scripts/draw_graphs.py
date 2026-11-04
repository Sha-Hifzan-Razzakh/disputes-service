"""Export the compiled graphs as Mermaid (T03.2 / T06.8): docs/graph_after.mmd is compared with the reviewed
design in docs/graph_target.mmd during review. Needs no database or API keys."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.orchestration.assist import build_assist_graph
from app.orchestration.evidence import build_evidence_graph
from app.orchestration.resolution import build_resolution_graph


def main() -> None:
    evidence = build_evidence_graph().compile(name="evidence")
    parts: dict[str, Any] = {
        "resolution": build_resolution_graph(evidence).compile(name="resolution"),
        "assist": build_assist_graph(evidence).compile(name="assist"),
    }
    out = []
    for name, g in parts.items():
        mmd = g.get_graph(xray=True).draw_mermaid()
        out.append(f"%% ---- {name} graph (compiled, xray) ----\n{mmd}")
        Path(f"docs/graph_after_{name}.mmd").write_text(mmd)
    print("wrote", ", ".join(f"docs/graph_after_{n}.mmd" for n in parts))


if __name__ == "__main__":
    main()
