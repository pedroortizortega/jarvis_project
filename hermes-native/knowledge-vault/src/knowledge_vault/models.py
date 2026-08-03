from dataclasses import dataclass
from uuid import uuid4
@dataclass(frozen=True)
class Proposal:
    id: str
    markdown: str
    idempotency_key: str
    provenance: dict[str, str]
    predecessor_id: str | None = None

    @classmethod
    def create(cls, markdown, idempotency_key, provenance, predecessor_id=None):
        return cls(str(uuid4()), markdown, idempotency_key, dict(provenance), predecessor_id)

    @classmethod
    def revise(cls, original, markdown, idempotency_key, *, rejected=False):
        if not rejected:
            raise ValueError("only rejected proposals may be revised")
        return cls.create(markdown, idempotency_key, original.provenance, original.id)
@dataclass(frozen=True)
class Decision:
    proposal_id: str
    version: int
    reviewer: str
    decision: str
    rationale: str


@dataclass(frozen=True)
class ApprovedRecord:
    proposal: Proposal
    decision: Decision


@dataclass(frozen=True)
class RetrievalHit:
    note_path: str
    fragment_id: str
    text: str
    score: float


@dataclass(frozen=True)
class RetrievalResult:
    hits: tuple
    available: bool
    reason: str


@dataclass(frozen=True)
class PublicationFailure:
    proposal_id: str
    reason: str
