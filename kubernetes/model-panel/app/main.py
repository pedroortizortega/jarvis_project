"""model-panel FastAPI app: bearer auth, `GET /api/status`, `POST
/api/switch`, `POST /api/repair`, `GET /healthz`, and the single-page UI
(D9).

Wires PR3's core (`app/handoff/*`, `app/clients/codex_shim.py`) into HTTP
routes. `POST /api/switch`'s D17 fail-closed precondition is checked
synchronously in the request handler (so an invalid Codex session returns a
proper `409 {session_state, reason}` before anything is scheduled); the
guarded multi-minute switch sequence itself then runs on a background
thread so `GET /api/status` keeps polling responsively while a switch is
in flight (spec: "Toggle disabled during in-progress switch").

Deviation (documented, not silent): `POST /api/switch`'s `transition_id` in
the 202 response is generated independently in this module rather than read
back from `steps.switch_to()`'s own internally-generated uuid, because
`switch_to()` is synchronous/blocking and only returns after the whole
sequence completes (or fails) — by which point the "in progress" id is
already meaningless. The UI's ground truth for progress is always
`GET /api/status`'s `phase`/`mode` fields, never the transition id; the id
is advisory/opaque, matching the design's data flow ("the panel shows
switch progress" via polling, not by round-tripping a client-picked id
through the backend).
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.clients.codex_shim import CodexShimClient, SwitchBlocked, assert_switch_to_cloud_allowed
from app.clients.llama_router import LlamaRouterClient
from app.handoff import steps
from app.handoff.gpu import gpu_free
from app.handoff.state import HandoffState, StateStore, reconcile_against_live

logger = logging.getLogger("model_panel")

AUTH_TOKEN_ENV = "MODEL_PANEL_AUTH_TOKEN"
NAMESPACE_DEFAULT = os.environ.get("MODEL_PANEL_NAMESPACE", "llms")
ROUTER_DEPLOYMENT = steps.ROUTER_DEPLOYMENT

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _BASE_DIR / "templates"
_STATIC_DIR = _BASE_DIR / "static"


def _check_bearer(request: Request) -> None:
    expected = os.environ.get(AUTH_TOKEN_ENV, "")
    if not expected:
        # Fail closed: an unset auth token must never mean "open" (same
        # pattern as codex-shim's `_check_internal_bearer`).
        raise HTTPException(status_code=503, detail="model-panel auth token not configured")
    auth_header = request.headers.get("authorization", "")
    provided = auth_header[len("Bearer "):] if auth_header.startswith("Bearer ") else ""
    if provided != expected:
        raise HTTPException(status_code=401, detail="invalid bearer token")


def _default_litellm_params_for(target: str) -> Dict[str, Any]:
    """Interim implementation for Phase 8 wiring, pending Phase 9's D-OQ2
    resolution (exact `CODEX_CLOUD_MODEL`) and the real `cloud` entry in
    `litellm-config.yaml`. Values are env-configurable at deploy time so no
    code change is needed once Phase 9 settles them; Phase 9 still owns
    adding the actual `cloud` model_list entry to the committed YAML."""
    if target == "cloud":
        return {
            "model": f"openai/{os.environ.get('CODEX_CLOUD_MODEL', 'gpt-5.1-codex')}",
            "api_base": os.environ.get(
                "CODEX_SHIM_BASE_URL", "http://codex-shim.llms.svc.cluster.local:8080/v1"
            ),
            "api_key": "os.environ/CODEX_SHIM_KEY",
        }
    return {
        "model": f"openai/{steps.FIXED_DEFAULT_MODEL_ALIAS}",
        "api_base": os.environ.get(
            "LLAMA_ROUTER_BASE_URL", "http://llama-router.llms.svc.cluster.local:8080/v1"
        ),
        "api_key": "os.environ/LLAMA_API_KEY",
    }


def _default_litellm_params_for_preset(preset: str) -> Dict[str, Any]:
    """D18: patches the `qwen3` alias's `litellm_params.model` to
    `openai/<preset>` — same `api_base`/`api_key` as the local target, just
    a different preset name (`switch_profile()` always targets the
    router)."""
    return {
        "model": f"openai/{preset}",
        "api_base": os.environ.get(
            "LLAMA_ROUTER_BASE_URL", "http://llama-router.llms.svc.cluster.local:8080/v1"
        ),
        "api_key": "os.environ/LLAMA_API_KEY",
    }


def _lazy_k8s_clients() -> tuple[Any, Any, Any]:
    from kubernetes import client, config as k8s_config

    try:
        k8s_config.load_incluster_config()
    except Exception:
        k8s_config.load_kube_config()
    return client.CoreV1Api(), client.AppsV1Api(), client.CustomObjectsApi()


def create_app(
    *,
    core_v1: Any = None,
    apps_v1: Any = None,
    custom_objects_api: Any = None,
    state_store: Optional[StateStore] = None,
    codex_shim_client: Any = None,
    fetch_router_slots: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    litellm_params_for: Optional[Callable[[str], Dict[str, Any]]] = None,
    preload_probe: Optional[Callable[[str], None]] = None,
    restart_litellm: Optional[Callable[[], None]] = None,
    router_client: Any = None,
    namespace: str = NAMESPACE_DEFAULT,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    app = FastAPI(title="model-panel", version="0.1.0")

    clients_holder: Dict[str, Any] = {}

    def get_clients() -> tuple[Any, Any, Any]:
        if core_v1 is not None and apps_v1 is not None and custom_objects_api is not None:
            return core_v1, apps_v1, custom_objects_api
        if "clients" not in clients_holder:
            clients_holder["clients"] = _lazy_k8s_clients()
        c1, c2, c3 = clients_holder["clients"]
        return (core_v1 or c1, apps_v1 or c2, custom_objects_api or c3)

    store = state_store or StateStore(core_v1=core_v1)
    shim_client = codex_shim_client
    router = router_client
    if shim_client is None or router is None:
        import httpx

        if shim_client is None:
            shim_client = CodexShimClient(http_client=httpx.Client())
        if router is None:
            router = LlamaRouterClient(
                http_client=httpx.Client(),
                base_url=os.environ.get(
                    "LLAMA_ROUTER_BASE_URL", "http://llama-router.llms.svc.cluster.local:8080/v1"
                ),
                api_key=os.environ.get("LLAMA_API_KEY", ""),
            )

    app.state.state_store = store
    app.state.codex_shim_client = shim_client
    app.state.router_client = router
    app.state.executor = ThreadPoolExecutor(max_workers=1)
    app.state.switch_lock = threading.Lock()

    if _TEMPLATES_DIR.is_dir():
        templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    else:
        templates = None
    if _STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    def build_ctx(target: str) -> steps.HandoffContext:
        c1, c2, c3 = get_clients()
        return steps.HandoffContext(
            core_v1=c1,
            apps_v1=c2,
            custom_objects_api=c3,
            fetch_router_slots=fetch_router_slots or (lambda: []),
            litellm_params_for=litellm_params_for or _default_litellm_params_for,
            codex_shim_client=shim_client,
            preload_probe=preload_probe,
            restart_litellm=restart_litellm,
            namespace=namespace,
            sleep=sleep,
            clock=clock,
            state_store=store,
        )

    def build_profile_ctx() -> steps.HandoffContext:
        c1, c2, c3 = get_clients()
        return steps.HandoffContext(
            core_v1=c1,
            apps_v1=c2,
            custom_objects_api=c3,
            fetch_router_slots=fetch_router_slots or (lambda: []),
            litellm_params_for=_default_litellm_params_for_preset,
            codex_shim_client=shim_client,
            preload_probe=preload_probe,
            restart_litellm=restart_litellm,
            router_client=router,
            namespace=namespace,
            sleep=sleep,
            clock=clock,
            state_store=store,
        )

    def run_switch_in_background(target: str) -> None:
        try:
            steps.switch_to(target, build_ctx(target))
        except SwitchBlocked:
            # Already checked synchronously before scheduling; a race here
            # (session invalidated between the pre-check and the background
            # run) is handled by switch_to()'s own internal degraded-state
            # write. Nothing further to do.
            logger.warning("model-panel: switch blocked during background run")
        except Exception:
            logger.exception("model-panel: switch_to(%s) failed", target)
        finally:
            app.state.switch_lock.release()

    def start_switch(target: str) -> JSONResponse:
        if target == "cloud":
            try:
                assert_switch_to_cloud_allowed(shim_client)
            except SwitchBlocked as exc:
                return JSONResponse(
                    status_code=409,
                    content={"session_state": exc.session_state, "reason": exc.reason},
                )

        acquired = app.state.switch_lock.acquire(blocking=False)
        if not acquired:
            return JSONResponse(
                status_code=409, content={"error": "transition_in_progress"}
            )

        transition_id = str(uuid.uuid4())
        try:
            app.state.executor.submit(run_switch_in_background, target)
        except Exception:
            app.state.switch_lock.release()
            raise
        return JSONResponse(status_code=202, content={"transition_id": transition_id})

    def run_profile_switch_in_background(profile: str) -> None:
        try:
            steps.switch_profile(profile, build_profile_ctx())
        except Exception:
            logger.exception("model-panel: switch_profile(%s) failed", profile)
        finally:
            app.state.switch_lock.release()

    def start_profile_switch(profile: str) -> JSONResponse:
        acquired = app.state.switch_lock.acquire(blocking=False)
        if not acquired:
            return JSONResponse(
                status_code=409, content={"error": "transition_in_progress"}
            )

        transition_id = str(uuid.uuid4())
        try:
            app.state.executor.submit(run_profile_switch_in_background, profile)
        except Exception:
            app.state.switch_lock.release()
            raise
        return JSONResponse(status_code=202, content={"transition_id": transition_id})

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/api/status")
    def api_status(request: Request) -> JSONResponse:
        _check_bearer(request)
        state = store.read()
        c1, c2, _c3 = get_clients()

        try:
            pods = list(c1.list_namespaced_pod(namespace).items)
        except Exception:
            pods = []
        gpu_pods_present = not gpu_free(pods)

        try:
            router_replicas = int(
                c2.read_namespaced_deployment_scale(ROUTER_DEPLOYMENT, namespace).spec.replicas
            )
        except Exception:
            router_replicas = 0

        drift_info = reconcile_against_live(
            state, router_replicas=router_replicas, gpu_pods_present=gpu_pods_present
        )

        session: Optional[Dict[str, Any]]
        try:
            session = shim_client.get_session_status()
        except Exception as exc:
            session = {"state": "unreachable", "reason": str(exc)}

        return JSONResponse(
            {
                "mode": state.mode,
                "profile": state.profile,
                "transitioning": state.phase == "transitioning",
                "phase": state.phase,
                "error": state.error,
                "gpu_pods": gpu_pods_present,
                "session": session,
                "last_known_good": state.last_known_good,
                "drift": drift_info["drift"],
            }
        )

    @app.post("/api/switch")
    async def api_switch(request: Request) -> JSONResponse:
        _check_bearer(request)
        body = await request.json()
        target = body.get("target")
        if target not in ("cloud", "local"):
            raise HTTPException(status_code=400, detail="target must be 'cloud' or 'local'")

        state = store.read()
        if state.phase == "transitioning":
            return JSONResponse(status_code=409, content={"error": "transition_in_progress"})

        return start_switch(target)

    @app.post("/api/profile")
    async def api_profile(request: Request) -> JSONResponse:
        """D18/D18a: guarded profile switch (daily<->large) while Local.
        All four preconditions are fail-closed with zero cluster
        mutations: 400 on an unknown profile, 409 `not_local` when
        `mode != "local"`, 409 `transition_in_progress` when a switch is
        already running, 200 `{"unchanged": true}` when the requested
        profile is already active."""
        _check_bearer(request)
        body = await request.json()
        profile = body.get("profile")
        if profile not in steps.PROFILE_MODEL_ALIASES:
            return JSONResponse(status_code=400, content={"error": "invalid_profile"})

        state = store.read()
        if state.mode != "local":
            return JSONResponse(status_code=409, content={"error": "not_local"})
        if state.phase == "transitioning":
            return JSONResponse(status_code=409, content={"error": "transition_in_progress"})
        if state.profile == profile:
            return JSONResponse(status_code=200, content={"unchanged": True})

        return start_profile_switch(profile)

    @app.post("/api/repair")
    def api_repair(request: Request) -> JSONResponse:
        _check_bearer(request)
        state = store.read()
        if state.phase != "degraded":
            raise HTTPException(status_code=400, detail="no degraded switch to repair")
        if not state.target:
            raise HTTPException(status_code=400, detail="no target recorded for repair")
        return start_switch(state.target)

    @app.get("/")
    def index(request: Request):
        if templates is None:
            return JSONResponse({"status": "ui not bundled"}, status_code=500)
        return templates.TemplateResponse(request, "index.html", {})

    return app


app = create_app()
