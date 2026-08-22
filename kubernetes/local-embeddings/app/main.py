"""FastAPI HTTP layer for the local-embeddings service.

Wiring only — `app/embeddings.py` (PR 1) owns every validation, batching,
and response-assembly decision; this module just: loads the model at
lifespan startup (D-01), routes the three endpoints, offloads inference to
the threadpool behind a serializing semaphore (D-05), and renders every
error as the OpenAI-shaped `{"error": {...}}` envelope, never FastAPI's
default `{"detail": ...}` shape (D-12). See design.md's Data Flow section.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import model
from app.embeddings import (
    DIMENSION,
    MODEL_ID,
    Embedder,
    EmbeddingError,
    build_response,
    validate_request,
)


def create_app(embedder: Optional[Embedder] = None) -> FastAPI:
    """Build the FastAPI app. `embedder=None` (production) lazily loads the
    pinned model in the lifespan; tests inject a fake embedder and skip the
    loader entirely (D-01)."""

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI):
        if embedder is not None:
            app_instance.state.embedder = embedder
        else:
            app_instance.state.embedder = None
            app_instance.state.embedder = await run_in_threadpool(model.load_embedder)
        yield

    app = FastAPI(title="local-embeddings", version="0.1.0", lifespan=lifespan)
    app.state.embedder = embedder
    app.state.inference_gate = asyncio.Semaphore(1)

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "the request body is malformed or invalid",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "invalid_request",
                }
            },
        )

    @app.exception_handler(EmbeddingError)
    async def _embedding_error_handler(request: Request, exc: EmbeddingError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=exc.to_body())

    @app.post("/v1/embeddings")
    async def embeddings(body: dict[str, Any]) -> JSONResponse:
        req = validate_request(body)

        embedder_instance = app.state.embedder
        if embedder_instance is None:
            raise EmbeddingError("model_not_ready", "the model has not finished loading", status=503)

        async with app.state.inference_gate:
            vectors = await run_in_threadpool(embedder_instance.embed, req.texts)
            token_counts = await run_in_threadpool(embedder_instance.count_tokens, req.texts)

        response_body = build_response(req, vectors, token_counts)
        return JSONResponse(response_body)

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        if app.state.embedder is None:
            return JSONResponse(status_code=503, content={"status": "loading"})
        return JSONResponse({"status": "ok", "model": MODEL_ID, "dimension": DIMENSION})

    @app.get("/v1/models")
    async def list_models() -> JSONResponse:
        return JSONResponse(
            {
                "object": "list",
                "data": [{"id": MODEL_ID, "object": "model", "owned_by": "local"}],
            }
        )

    return app


app = create_app()
