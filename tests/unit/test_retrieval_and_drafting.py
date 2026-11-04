from app.application.ports import PolicyHit
from app.infrastructure.drafting import RuleBasedDrafter
from app.infrastructure.retrieval import HashingEmbedder, chunk_text


def test_embedder_is_deterministic_and_normalised() -> None:
    e = HashingEmbedder(64)
    a, b = e.embed("proof of delivery tracking"), e.embed("proof of delivery tracking")
    assert a == b
    assert abs(sum(x * x for x in a) - 1.0) < 1e-9


def test_similar_text_scores_higher() -> None:
    e = HashingEmbedder()
    q = e.embed("parcel never arrived tracking delivered")
    near = e.embed("carrier tracking record shows the parcel delivered")
    far = e.embed("restocking fee for opened electronics")
    dot = lambda x, y: sum(i * j for i, j in zip(x, y, strict=True))  # noqa: E731
    assert dot(q, near) > dot(q, far)


def test_chunking_splits_long_paragraphs_with_overlap() -> None:
    text = " ".join(f"w{i}" for i in range(200))
    chunks = chunk_text(text, max_words=90, overlap=15)
    assert len(chunks) == 3 and chunks[1].split()[0] == "w75"


async def test_rule_drafter_contests_strong_fraud_evidence() -> None:
    case = {
        "reason_family": "FRAUD",
        "evidence_kinds": [],
        "transaction": {"three_ds_authenticated": True, "avs_result": "Y", "cvv_result": "M"},
    }
    hit = PolicyHit(chunk_id="c1", document_title="d", text="3-D Secure shifts liability", score=0.5, scope="network")
    rec = await RuleBasedDrafter().draft(case=case, question="contest?", hits=[hit])
    assert rec.suggested_resolution == "CONTEST" and rec.citations == ["c1"]


async def test_rule_drafter_accepts_undelivered() -> None:
    case = {
        "reason_family": "NOT_RECEIVED",
        "evidence_kinds": [],
        "transaction": {"delivery_status": "IN_TRANSIT", "delivery_tracking": "X"},
    }
    rec = await RuleBasedDrafter().draft(case=case, question="q", hits=[])
    assert rec.suggested_resolution == "ACCEPT"
