from dataclasses import dataclass


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
