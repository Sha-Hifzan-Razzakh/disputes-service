"""T27: the concern table is generated from its single source, and every proof it cites still exists."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("concerns", ROOT / "ops/concerns.py")
assert _spec and _spec.loader
concerns = importlib.util.module_from_spec(_spec)
sys.modules["concerns"] = concerns
_spec.loader.exec_module(concerns)


def test_doc_is_generated_from_the_source() -> None:
    assert (ROOT / "docs/concerns.md").read_text() == concerns.render(), "run: python ops/concerns.py"


def test_ids_unique_and_controlled_rows_have_proof() -> None:
    ids = [c.id for c in concerns.CONCERNS]
    assert len(ids) == len(set(ids))
    for c in concerns.CONCERNS:
        assert c.status in {"controlled", "accepted"}
        assert c.proofs or c.status == "accepted", c.id


@pytest.mark.parametrize("ref", sorted({p for c in concerns.CONCERNS for p in c.proofs}))
def test_every_proof_exists(ref: str) -> None:
    kind, _, rest = ref.partition(":")
    if kind == "evals":
        ids = {
            json.loads(x)["id"] for p in (ROOT / "evals").glob("golden_v*.jsonl") for x in p.read_text().splitlines()
        }
        assert rest in ids
    elif kind == "ci":
        workflow, job = rest.split("/")
        import yaml

        assert job in yaml.safe_load((ROOT / f".github/workflows/{workflow}.yml").read_text())["jobs"]
    elif kind == "drill":
        assert (ROOT / rest).exists()
    else:
        module, _, attr = ref.partition("::")
        obj: object = importlib.import_module(module)
        for part in attr.split("::"):
            obj = getattr(obj, part)
        assert callable(obj)
