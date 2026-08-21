"""Pure request/response core for the local-embeddings service.

No third-party imports. No I/O. No `fastembed`. This module holds only
validation, batching rules, response/usage assembly, and the error
taxonomy — the HTTP layer (`app/main.py`, PR 2) and the ONNX adapter
(`app/model.py`, this PR's Phase 2) are the only callers, both via the
`Embedder` protocol. See design.md D-06..D-11 and the Interfaces /
Data Flow sections for the contract this module implements.
"""
from __future__ import annotations

from typing import Protocol

MODEL_ID = "intfloat/multilingual-e5-small"
DIMENSION = 384
MAX_BATCH = 256

# D-10 (decided 2026-08-21): opt-in query/passage prefixing. Omitted
# `input_type` embeds verbatim — this map only ever applies when the
# caller explicitly opts in.
INPUT_TYPE_PREFIXES = {
    "query": "query: ",
    "passage": "passage: ",
}


class Embedder(Protocol):
    """Implemented by `app.model.FastEmbedAdapter` (real) and test fakes."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def count_tokens(self, texts: list[str]) -> list[int]: ...


class EmbeddingError(Exception):
    """Carries the OpenAI-shaped `{"error": {...}}` envelope fields (D-12).

    The HTTP layer renders `to_body()` at `status` — this module never
    touches FastAPI or HTTP status codes directly.
    """

    def __init__(
        self,
        code: str,
        message: str,
        param: str | None = None,
        status: int = 400,
        type_: str = "invalid_request_error",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.param = param
        self.status = status
        self.type_ = type_

    def to_body(self) -> dict:
        return {
            "error": {
                "message": self.message,
                "type": self.type_,
                "param": self.param,
                "code": self.code,
            }
        }


class EmbeddingRequest:
    """A validated request: texts ready for `Embedder.embed` (already
    prefixed per `input_type`, if any) plus the client's `model` string
    to echo back verbatim (D-09)."""

    __slots__ = ("texts", "echo_model")

    def __init__(self, texts: list[str], echo_model: str) -> None:
        self.texts = texts
        self.echo_model = echo_model


def validate_request(body: dict) -> EmbeddingRequest:
    """Parse and validate an incoming `/v1/embeddings` body.

    Raises `EmbeddingError` (never a bare exception) for every invalid
    shape so the HTTP layer can render one consistent OpenAI-style body.
    """
    raw_input = body.get("input")
    if raw_input is None:
        raise EmbeddingError("invalid_input", "input is required", param="input")

    if isinstance(raw_input, str):
        items = [raw_input]
    elif isinstance(raw_input, list) and all(isinstance(item, str) for item in raw_input):
        items = list(raw_input)
    else:
        raise EmbeddingError(
            "invalid_input",
            "input must be a string or a list of strings",
            param="input",
        )

    if len(items) == 0:
        raise EmbeddingError("invalid_input", "input must not be empty", param="input")

    if any(item == "" for item in items):
        raise EmbeddingError(
            "invalid_input", "input must not contain empty strings", param="input"
        )

    if len(items) > MAX_BATCH:
        raise EmbeddingError(
            "batch_size_exceeded",
            f"batch size {len(items)} exceeds the maximum of {MAX_BATCH}",
            param="input",
        )

    encoding_format = body.get("encoding_format", "float")
    if encoding_format != "float":
        raise EmbeddingError(
            "unsupported_encoding_format",
            f"encoding_format {encoding_format!r} is not supported; only 'float' is",
            param="encoding_format",
        )

    dimensions = body.get("dimensions")
    if dimensions is not None and dimensions != DIMENSION:
        raise EmbeddingError(
            "dimension_mismatch",
            f"dimensions {dimensions!r} does not match the pinned dimension {DIMENSION}",
            param="dimensions",
        )

    input_type = body.get("input_type")
    if input_type is not None and input_type not in INPUT_TYPE_PREFIXES:
        raise EmbeddingError(
            "invalid_input_type",
            f"input_type {input_type!r} must be one of 'query', 'passage'",
            param="input_type",
        )

    prefix = INPUT_TYPE_PREFIXES.get(input_type, "")
    texts = [prefix + item for item in items]

    echo_model = body.get("model") or MODEL_ID

    return EmbeddingRequest(texts=texts, echo_model=echo_model)


def build_response(
    req: EmbeddingRequest, vectors: list[list[float]], token_counts: list[int]
) -> dict:
    """Assemble the OpenAI-shaped response body.

    Hard invariant (D-07): a vector of the wrong length is never padded
    or truncated — it raises a 500 `EmbeddingError` instead, because a
    silently reshaped vector is a permanently poisoned index entry.
    """
    if len(vectors) != len(req.texts):
        raise EmbeddingError(
            "internal_error",
            "embedder returned a different number of vectors than inputs",
            status=500,
            type_="internal_error",
        )

    data = []
    for index, vector in enumerate(vectors):
        if len(vector) != DIMENSION:
            raise EmbeddingError(
                "internal_error",
                f"embedder returned a {len(vector)}-dimension vector, "
                f"expected the pinned dimension {DIMENSION}",
                status=500,
                type_="internal_error",
            )
        data.append({"object": "embedding", "index": index, "embedding": vector})

    prompt_tokens = sum(token_counts)
    return {
        "object": "list",
        "data": data,
        "model": req.echo_model,
        "usage": {"prompt_tokens": prompt_tokens, "total_tokens": prompt_tokens},
    }
