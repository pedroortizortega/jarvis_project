from __future__ import annotations

import json
import http.client
import logging
import os
import shutil
import signal
import sqlite3
import subprocess
import threading
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping
from urllib.parse import urlparse

from .policy import Classification, Decision, RouterPolicy


logger = logging.getLogger(__name__)
_DEPTH_ENV = "HERMES_ORCHESTRATION_DEPTH"
_STATE_TTL_SECONDS = 600
_MAX_PENDING_TURNS = 256
_MAX_TURN_STATES = 512
_LOCAL_ONLY_TOOL_NAMES = frozenset(
    {"read_file", "write_file", "patch", "search_files", "todo"}
)


class OrchestrationRuntime:
    def __init__(self, ctx: Any, root: Path) -> None:
        self.ctx = ctx
        self.root = root
        self.plugin_id = ctx.manifest.key or ctx.manifest.name
        self._pending: dict[str, tuple[str, Decision, dict[str, Any], float]] = {}
        self._turn_state: dict[str, tuple[Decision, dict[str, Any], float]] = {}
        self._lock = threading.RLock()
        self._policy: RouterPolicy | None = None
        self._policy_path = ""
        self._overloaded_until = 0.0
        self._worker_slots = threading.BoundedSemaphore(2)
        self._sol_slot = threading.BoundedSemaphore(1)

    def pre_llm_call(self, **kwargs: Any) -> None:
        if os.environ.get(_DEPTH_ENV) or not kwargs.get("turn_id"):
            return None
        config = self._config()
        mode = str(config.get("mode", "shadow")).strip().lower()
        if mode == "disabled":
            return None
        platforms = config.get("platforms", ["cli", "telegram"])
        if isinstance(platforms, list) and kwargs.get("platform") not in platforms:
            return None
        text = self._message_text(kwargs.get("user_message"))
        if not text:
            return None

        policy = self._get_policy(config)
        semantic, classifier_unavailable = self._semantic_classification(text, policy, config)
        decision = policy.decide(text, semantic=semantic, mode=mode)
        if (
            classifier_unavailable
            and decision.explicit_override
            and mode in {"explicit", "auto"}
            and bool(config.get("require_classifier_for_explicit", True))
        ):
            decision = Decision(
                decision.classification,
                decision.proposed_route,
                decision.final_route,
                "explicit_classifier_unavailable",
                True,
                False,
            )
        if (
            decision.should_delegate
            and (decision.final_route == "sol-high" or decision.classification.risk == "high")
            and not bool(config.get("allow_high_risk_auto", False))
            and not decision.explicit_override
        ):
            decision = Decision(
                decision.classification,
                decision.proposed_route,
                decision.final_route,
                "confirmation_required",
                False,
                False,
            )
        metadata = {
            "session_id": str(kwargs.get("session_id") or ""),
            "task_id": str(kwargs.get("task_id") or ""),
            "turn_id": str(kwargs.get("turn_id") or ""),
            "platform": str(kwargs.get("platform") or ""),
            "mode": mode,
        }
        now = time.monotonic()
        with self._lock:
            self._prune_state(now)
            state_stored = len(self._turn_state) < _MAX_TURN_STATES
            if state_stored:
                self._turn_state[metadata["turn_id"]] = (decision, metadata, now)
            else:
                self._overloaded_until = max(self._overloaded_until, now + _STATE_TTL_SECONDS)
                self._audit("state_overflow", decision, metadata)
            if state_stored and len(self._pending) < _MAX_PENDING_TURNS:
                stored_text = text if decision.should_delegate else ""
                self._pending[metadata["turn_id"]] = (stored_text, decision, metadata, now)
            elif state_stored:
                self._audit("pending_overflow", decision, metadata)
        self._audit("classifier_unavailable" if classifier_unavailable else "classified", decision, metadata)
        return None

    def llm_execution(self, **kwargs: Any) -> Any:
        request = kwargs.get("request") or {}
        next_call = kwargs.get("next_call")
        if not callable(next_call):
            return request
        if os.environ.get(_DEPTH_ENV):
            return next_call(request)
        turn_id = str(kwargs.get("turn_id") or "")
        with self._lock:
            now = time.monotonic()
            turn_state = self._turn_state.get(turn_id)
            if turn_state is not None:
                turn_decision, turn_metadata, _created_at = turn_state
                self._turn_state[turn_id] = (turn_decision, turn_metadata, now)
                pending = self._pending.get(turn_id)
                if pending is not None:
                    text, pending_decision, pending_metadata, _pending_at = pending
                    self._pending[turn_id] = (text, pending_decision, pending_metadata, now)
            self._prune_state(now)
            overloaded = now < self._overloaded_until
        if turn_state is not None:
            turn_decision, turn_metadata, _created_at = turn_state
            if turn_decision.classification.privacy == "local_only":
                try:
                    response = self._local_completion(request, self._config())
                except Exception as exc:
                    logger.warning("local-only execution failed type=%s", type(exc).__name__)
                    self._audit("privacy_local_failed", turn_decision, turn_metadata, type(exc).__name__)
                    return self._synthetic_response(
                        "Ruta: local | no disponible\n\n"
                        "El endpoint local no pudo completar la solicitud. No se contacto ningun proveedor cloud.",
                        kwargs.get("model"),
                        turn_id,
                    )
                self._audit("privacy_local", turn_decision, turn_metadata)
                return response
        elif overloaded:
            try:
                return self._local_completion(request, self._config())
            except Exception as exc:
                logger.warning("overload local execution failed type=%s", type(exc).__name__)
                return self._synthetic_response(
                    "Ruta: local | proteccion por sobrecarga\n\n"
                    "El router alcanzo su limite de estado y el endpoint local no respondio. No se uso cloud.",
                    kwargs.get("model"),
                    turn_id,
                )
        if kwargs.get("api_mode") != "chat_completions":
            with self._lock:
                self._pending.pop(str(kwargs.get("turn_id") or ""), None)
            return next_call(request)
        if int(kwargs.get("api_call_count") or 0) != 1:
            return next_call(request)

        with self._lock:
            pending = self._pending.pop(turn_id, None)
        if pending is None:
            return next_call(request)
        text, decision, metadata, _created_at = pending
        if decision.final_route == "local_large" and metadata.get("mode") != "shadow":
            self._audit("local_large_unavailable", decision, metadata)
            return self._synthetic_response(
                "Ruta: local_large | no disponible\n\n"
                "El coordinador exclusivo para Qwen 27B aun no esta habilitado; se mantuvo Qwen 9B.",
                kwargs.get("model"),
                turn_id,
            )
        if decision.rule == "explicit_classifier_unavailable":
            self._audit("explicit_blocked", decision, metadata)
            return self._synthetic_response(
                f"Ruta: {decision.final_route} | bloqueada\n\n"
                "El clasificador local de privacidad no estuvo disponible. La tarea no se envio al perfil cloud.",
                kwargs.get("model"),
                turn_id,
            )
        if not decision.should_delegate:
            self._audit("local", decision, metadata)
            return next_call(request)

        route = decision.final_route
        config = self._config()
        try:
            response_text = self._run_worker(text, decision, config)
        except Exception as exc:
            logger.warning("intent orchestration worker failed route=%s type=%s", route, type(exc).__name__)
            self._audit("worker_failed", decision, metadata, error_type=type(exc).__name__)
            if decision.explicit_override:
                return self._synthetic_response(
                    f"Ruta: {route} | no disponible\n\n"
                    "No se pudo ejecutar el perfil solicitado. La tarea no se envio a otro perfil.",
                    kwargs.get("model"),
                    turn_id,
                )
            return next_call(request)

        self._audit("delegated", decision, metadata)
        tools = ", ".join(decision.classification.needs_tools) or "sin herramientas"
        return self._synthetic_response(
            f"Ruta: {route} | {decision.classification.task_class} | {tools}\n\n{response_text}",
            kwargs.get("model"),
            turn_id,
        )

    def _semantic_classification(
        self, text: str, policy: RouterPolicy, config: Mapping[str, Any]
    ) -> tuple[Classification | None, bool]:
        if not bool(config.get("semantic_classifier", True)) or not self._classifier_is_local(config):
            return None, True
        signals = policy.signals(text)
        if signals.local_only or signals.explicit_local_large:
            return None, False
        try:
            schema = json.loads(self._asset_path("classifier-schema.json").read_text(encoding="utf-8"))
            prompt = self._asset_path("classifier-prompt.md").read_text(encoding="utf-8")
            model_config = self._main_model_config()
            body = {
                "model": str(model_config.get("default") or ""),
                "messages": [
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": f"JSON schema:\n{json.dumps(schema, sort_keys=True)}\n\nRequest:\n{text}",
                    },
                ],
                "temperature": 0,
                "max_tokens": int(config.get("classifier_max_tokens", 320)),
                "response_format": {"type": "json_object"},
                "chat_template_kwargs": {"enable_thinking": False},
            }
            parsed = self._local_json_request(
                str(model_config.get("base_url") or ""),
                body,
                float(config.get("classifier_timeout_seconds", 15)),
            )
            content = parsed["choices"][0]["message"]["content"]
            value = json.loads(content)
            if not isinstance(value, dict):
                raise ValueError("classifier did not return a JSON object")
            return Classification.from_mapping(value, policy.allowed_routes), False
        except Exception as exc:
            logger.warning("intent classifier fallback type=%s", type(exc).__name__)
            return None, True

    @staticmethod
    def _local_json_request(base_url: str, body: Mapping[str, Any], timeout: float) -> dict[str, Any]:
        parsed_url = urlparse(base_url.rstrip("/") + "/chat/completions")
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
            raise ValueError("invalid local classifier URL")
        connection_type = http.client.HTTPSConnection if parsed_url.scheme == "https" else http.client.HTTPConnection
        port = parsed_url.port or (443 if parsed_url.scheme == "https" else 80)
        connection = connection_type(parsed_url.hostname, port, timeout=timeout)
        api_key = os.environ.get("OPENAI_API_KEY", "")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        path = parsed_url.path or "/"
        try:
            connection.request("POST", path, body=json.dumps(body).encode("utf-8"), headers=headers)
            response = connection.getresponse()
            payload = response.read(1_048_577)
        finally:
            connection.close()
        if len(payload) > 1_048_576:
            raise ValueError("classifier response exceeded 1 MiB")
        if not 200 <= response.status < 300:
            raise RuntimeError(f"local classifier returned HTTP {response.status}")
        value = json.loads(payload.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("local classifier response must be an object")
        return value

    def _run_worker(self, text: str, decision: Decision, config: Mapping[str, Any]) -> str:
        policy = self._get_policy(config)
        budget = policy.budget_for(decision.final_route)
        timeout = float(config.get("worker_timeout_seconds", budget.get("timeout_seconds", 300)))
        max_sources = int(budget.get("max_sources", 0))
        if any(
            capability in decision.classification.needs_tools
            for capability in ("files", "terminal", "tests")
        ) and not bool(config.get("allow_terminal_workers", False)):
            raise PermissionError("terminal workers require an explicit sandbox opt-in")
        packet = self._task_packet(text, decision, timeout, max_sources)
        hermes = shutil.which("hermes")
        if not hermes:
            raise FileNotFoundError("hermes executable not found")
        toolsets = self._toolsets_for(decision.classification.needs_tools)
        command = [
            hermes,
            "-p",
            decision.final_route,
            "--cli",
            "--toolsets",
            ",".join(toolsets),
            "--ignore-rules",
            "chat",
            "-q",
            packet,
            "-Q",
            "--source",
            "orchestration",
        ]
        inherited_names = ("HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR")
        env = {name: os.environ[name] for name in inherited_names if name in os.environ}
        env.setdefault("HOME", str(Path.home()))
        env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
        env["PYTHONUTF8"] = "1"
        env[_DEPTH_ENV] = "1"
        env.pop("HERMES_TUI", None)
        cwd = str(config.get("worker_cwd") or os.getcwd())
        route_slot = self._sol_slot if decision.final_route.startswith("sol-") else None
        if not self._worker_slots.acquire(blocking=False):
            raise RuntimeError("worker concurrency limit reached")
        if route_slot is not None and not route_slot.acquire(blocking=False):
            self._worker_slots.release()
            raise RuntimeError("Sol concurrency limit reached")
        try:
            try:
                process = subprocess.Popen(
                    command,
                    cwd=cwd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
                try:
                    stdout, _stderr = process.communicate(timeout=timeout)
                except BaseException:
                    self._terminate_process(process)
                    raise
            finally:
                if route_slot is not None:
                    route_slot.release()
                self._worker_slots.release()
        except Exception:
            raise
        if process.returncode != 0:
            raise RuntimeError(f"worker exited with status {process.returncode}")
        output = stdout.strip()
        if not output:
            raise RuntimeError("worker returned empty output")
        return output

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()

    @staticmethod
    def _task_packet(text: str, decision: Decision, timeout: float, max_sources: int) -> str:
        classification = decision.classification
        tools = ", ".join(classification.needs_tools) or "none"
        citations = "required" if "citations" in classification.needs_tools else "not required"
        return (
            "You are an isolated Hermes worker. Complete only the bounded task below and return the final "
            "result to the primary agent. Do not delegate the whole task again.\n\n"
            f"orchestration_depth: 1\nclass: {classification.task_class}\n"
            f"complexity: {classification.complexity}\nrisk: {classification.risk}\n"
            f"privacy: {classification.privacy}\nallowed_capabilities: {tools}\n"
            f"citations: {citations}\ntime_budget_seconds: {int(timeout)}\n"
            f"source_budget: {max_sources}\n\nObjective:\n{text}\n\n"
            "Completion criteria: answer the objective, respect the listed constraints, state blockers, and do not "
            "include secrets or unrelated conversation context."
        )

    @staticmethod
    def _synthetic_response(content: str, model: Any, turn_id: str) -> Any:
        return SimpleNamespace(
            id=f"orchestration-{turn_id}",
            model=str(model or "orchestration"),
            usage=None,
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(role="assistant", content=content, tool_calls=None),
                )
            ],
        )

    def _get_policy(self, config: Mapping[str, Any]) -> RouterPolicy:
        path = str(config.get("policy_path") or self._asset_path("policy.yaml"))
        with self._lock:
            if self._policy is None or self._policy_path != path:
                self._policy = RouterPolicy.from_path(path)
                self._policy_path = path
            return self._policy

    def _asset_path(self, name: str) -> Path:
        source_asset = self.root / name
        if source_asset.is_file():
            return source_asset
        return Path(__file__).resolve().parent / "data" / name

    def _classifier_is_local(self, config: Mapping[str, Any]) -> bool:
        try:
            model = self._main_model_config()
            base_url = str(model.get("base_url") or "").rstrip("/")
            allowed = config.get("local_base_urls", [])
            return isinstance(allowed, list) and base_url in {str(item).rstrip("/") for item in allowed}
        except Exception:
            return False

    def _local_completion(self, request: Mapping[str, Any], config: Mapping[str, Any]) -> Any:
        model_config = self._main_model_config()
        base_url = str(model_config.get("base_url") or "").rstrip("/")
        allowed = config.get("local_base_urls", [])
        if not isinstance(allowed, list) or base_url not in {str(item).rstrip("/") for item in allowed}:
            raise PermissionError("local endpoint is not allowlisted")
        allowed_keys = {
            "messages", "tools", "tool_choice", "parallel_tool_calls", "temperature",
            "max_tokens", "max_completion_tokens", "stop", "response_format", "seed",
        }
        body = {key: value for key, value in request.items() if key in allowed_keys and value is not None}
        if isinstance(body.get("tools"), list):
            body["tools"] = [tool for tool in body["tools"] if self._tool_name(tool) in _LOCAL_ONLY_TOOL_NAMES]
            if not body["tools"]:
                body.pop("tools")
                body.pop("tool_choice", None)
        body["model"] = str(model_config.get("default") or request.get("model") or "")
        body["stream"] = False
        result = self._local_json_request(
            base_url,
            body,
            float(config.get("local_request_timeout_seconds", 180)),
        )
        return self._to_namespace(result)

    @staticmethod
    def _tool_name(tool: Any) -> str:
        if not isinstance(tool, dict):
            return ""
        function = tool.get("function")
        return str(function.get("name") or "") if isinstance(function, dict) else ""

    @classmethod
    def _to_namespace(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return SimpleNamespace(**{key: cls._to_namespace(item) for key, item in value.items()})
        if isinstance(value, list):
            return [cls._to_namespace(item) for item in value]
        return value

    def finish_turn(self, **kwargs: Any) -> None:
        turn_id = str(kwargs.get("turn_id") or "")
        if not turn_id:
            return
        with self._lock:
            self._pending.pop(turn_id, None)
            self._turn_state.pop(turn_id, None)

    def finish_session(self, **kwargs: Any) -> None:
        session_id = str(kwargs.get("session_id") or "")
        if not session_id:
            return
        with self._lock:
            pending_turns = [
                turn_id for turn_id, (_text, _decision, metadata, _created) in self._pending.items()
                if metadata.get("session_id") == session_id
            ]
            state_turns = [
                turn_id for turn_id, (_decision, metadata, _created) in self._turn_state.items()
                if metadata.get("session_id") == session_id
            ]
            for turn_id in set(pending_turns + state_turns):
                self._pending.pop(turn_id, None)
                self._turn_state.pop(turn_id, None)

    def _prune_state(self, now: float) -> None:
        stale_pending = [
            turn_id for turn_id, (_text, _decision, _metadata, created) in self._pending.items()
            if now - created > _STATE_TTL_SECONDS
        ]
        for turn_id in stale_pending:
            self._pending.pop(turn_id, None)
        if now >= self._overloaded_until:
            self._overloaded_until = 0.0

    @staticmethod
    def _main_model_config() -> dict[str, Any]:
        from hermes_cli.config import load_config

        root = load_config() or {}
        model = root.get("model") or {}
        return dict(model) if isinstance(model, dict) else {}

    @staticmethod
    def _toolsets_for(capabilities: tuple[str, ...]) -> tuple[str, ...]:
        selected: list[str] = []
        if any(item in capabilities for item in ("web_search", "web_extract", "citations", "deep_research")):
            selected.append("web")
        if "browser" in capabilities:
            selected.append("browser")
        if any(item in capabilities for item in ("files", "terminal", "tests")):
            selected.append("terminal")
        return tuple(dict.fromkeys(selected)) or ("context_engine",)

    def _config(self) -> dict[str, Any]:
        try:
            from hermes_cli.config import load_config

            root = load_config() or {}
            entries = ((root.get("plugins") or {}).get("entries") or {})
            value = entries.get(self.plugin_id) or {}
            return dict(value) if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _audit(
        self,
        status: str,
        decision: Decision,
        metadata: Mapping[str, Any],
        error_type: str = "",
    ) -> None:
        config = self._config()
        if not bool(config.get("audit_enabled", True)):
            return
        try:
            audit_db = config.get("audit_db")
            if not audit_db:
                from hermes_constants import get_hermes_home

                audit_db = get_hermes_home() / "orchestration" / "events.sqlite3"
            path = Path(str(audit_db))
            path.parent.mkdir(parents=True, exist_ok=True)
            classification = asdict(decision.classification)
            with sqlite3.connect(path, timeout=5) as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS routing_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at REAL NOT NULL,
                        session_id TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        platform TEXT NOT NULL,
                        mode TEXT NOT NULL,
                        status TEXT NOT NULL,
                        task_class TEXT NOT NULL,
                        complexity TEXT NOT NULL,
                        risk TEXT NOT NULL,
                        privacy TEXT NOT NULL,
                        proposed_route TEXT NOT NULL,
                        final_route TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        rule TEXT NOT NULL,
                        explicit_override INTEGER NOT NULL,
                        error_type TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO routing_events (
                        created_at, session_id, task_id, turn_id, platform, mode, status,
                        task_class, complexity, risk, privacy, proposed_route, final_route,
                        confidence, rule, explicit_override, error_type
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        time.time(), metadata.get("session_id", ""), metadata.get("task_id", ""),
                        metadata.get("turn_id", ""), metadata.get("platform", ""), metadata.get("mode", ""),
                        status, classification["task_class"], classification["complexity"], classification["risk"],
                        classification["privacy"], decision.proposed_route, decision.final_route,
                        classification["confidence"], decision.rule, int(decision.explicit_override), error_type,
                    ),
                )
        except Exception as exc:
            logger.warning("intent orchestration audit failed type=%s", type(exc).__name__)

    @staticmethod
    def _message_text(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            parts = [item.get("text", "") for item in value if isinstance(item, dict) and item.get("type") == "text"]
            return "\n".join(str(part) for part in parts if part).strip()
        return ""
