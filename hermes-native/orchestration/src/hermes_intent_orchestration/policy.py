from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

import yaml


TASK_CLASSES = frozenset(
    {"chat", "lookup", "research", "deep_research", "coding", "review", "incident", "local_large"}
)
LEVELS = frozenset({"low", "medium", "high"})
PRIVACY_LEVELS = frozenset({"local_only", "cloud_allowed"})
TOOLS = frozenset(
    {"web_search", "web_extract", "browser", "files", "terminal", "tests", "citations", "deep_research"}
)
LOCAL_ROUTES = frozenset({"local", "local_large"})


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", ascii_text.lower()).strip()


def _has_any(text: str, phrases: tuple[str, ...]) -> bool:
    patterns = (re.escape(phrase).replace("\\ ", "\\s+") for phrase in phrases)
    return any(re.search(rf"(?<!\w){pattern}(?!\w)", text) for pattern in patterns)


def _has_pattern(text: str, pattern: str) -> bool:
    return re.search(rf"(?<!\w)(?:{pattern})(?!\w)", text) is not None


@dataclass(frozen=True)
class Classification:
    task_class: str
    complexity: str
    needs_current_data: bool
    needs_tools: tuple[str, ...]
    privacy: str
    risk: str
    route: str
    confidence: float
    reason: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], allowed_routes: set[str]) -> "Classification":
        task_class = str(value.get("task_class", ""))
        complexity = str(value.get("complexity", ""))
        privacy = str(value.get("privacy", ""))
        risk = str(value.get("risk", ""))
        route = str(value.get("route", ""))
        raw_tools = value.get("needs_tools", [])
        if task_class not in TASK_CLASSES:
            raise ValueError(f"invalid task_class: {task_class}")
        if complexity not in LEVELS or risk not in LEVELS:
            raise ValueError("invalid complexity or risk")
        if privacy not in PRIVACY_LEVELS:
            raise ValueError(f"invalid privacy: {privacy}")
        if route not in allowed_routes:
            raise ValueError(f"route is not allowlisted: {route}")
        if not isinstance(raw_tools, list) or any(tool not in TOOLS for tool in raw_tools):
            raise ValueError("needs_tools contains a non-allowlisted tool")
        confidence = float(value.get("confidence", -1))
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        reason = str(value.get("reason", "")).strip()
        if not reason or len(reason) > 160:
            raise ValueError("reason must contain 1 to 160 characters")
        return cls(
            task_class=task_class,
            complexity=complexity,
            needs_current_data=bool(value.get("needs_current_data", False)),
            needs_tools=tuple(dict.fromkeys(str(tool) for tool in raw_tools)),
            privacy=privacy,
            risk=risk,
            route=route,
            confidence=confidence,
            reason=reason,
        )


@dataclass(frozen=True)
class Signals:
    local_only: bool = False
    no_tools: bool = False
    explicit_profile: str = ""
    explicit_local_large: bool = False
    explicit_auto: bool = False
    deep_research: bool = False


@dataclass(frozen=True)
class Decision:
    classification: Classification
    proposed_route: str
    final_route: str
    rule: str
    explicit_override: bool
    should_delegate: bool


class RouterPolicy:
    def __init__(self, config: Mapping[str, Any]) -> None:
        profiles = config.get("profiles", [])
        if not isinstance(profiles, list) or not profiles:
            raise ValueError("policy profiles must be a non-empty list")
        self.profiles = frozenset(str(profile) for profile in profiles)
        self.allowed_routes = set(self.profiles) | set(LOCAL_ROUTES)
        self.routes = config.get("routes", {})
        self.economical_routes = config.get("economical_routes", {})
        confidence = config.get("confidence", {})
        self.apply_confidence = float(confidence.get("apply", 0.80))
        self.economical_confidence = float(confidence.get("economical", 0.55))
        self.budgets = config.get("budgets", {})

    @classmethod
    def from_path(cls, path: str | Path) -> "RouterPolicy":
        with Path(path).open("r", encoding="utf-8") as handle:
            value = yaml.safe_load(handle) or {}
        if not isinstance(value, dict):
            raise ValueError("policy root must be a mapping")
        return cls(value)

    def signals(self, text: str) -> Signals:
        normalized = normalize_text(text)
        aliases = {
            profile.replace("-", separator): profile
            for profile in self.profiles
            for separator in ("-", " ")
        }
        alias_pattern = "|".join(
            re.escape(alias) for alias in sorted(aliases, key=len, reverse=True)
        )
        profile_match = re.search(
            rf"\b(?:usa|use|utiliza|route to|ruta(?:\s+a)?|responde\s+con)\s+(?:a\s+)?({alias_pattern})\b",
            normalized,
        )
        if profile_match is None:
            profile_match = re.search(rf"\bwith\s+({alias_pattern})\b", normalized)
        if profile_match is None:
            profile_match = re.search(rf"^({alias_pattern})\s*:", normalized)
        explicit_profile = aliases.get(profile_match.group(1), "") if profile_match else ""
        return Signals(
            local_only=_has_any(
                normalized,
                (
                    "solo local", "local only", "no cloud", "sin nube", "no enviar a la nube",
                    "private document", "documento privado", "mis contrasenas", "my passwords",
                    "datos personales", "personal data", "mis credenciales", "my credentials",
                    "ssh key", "private key", "clave privada", "api key", "password", "contrasena",
                    "credential", "credentials", "secret", "secrets", "token privado", "private token",
                    "confidential", "confidencial", "sensitive data", "datos sensibles", "customer data",
                    "customer database", "client data", "social security", "ssn", "aws access key",
                    "aws_access_key_id", "access key id",
                )
            ),
            no_tools=_has_any(normalized, ("sin herramientas", "no tools", "without tools")),
            explicit_profile=explicit_profile,
            explicit_local_large=_has_any(
                normalized, ("local_large", "local large", "qwen 27b", "qwen3.6-27b", "modelo local 27b")
            ),
            explicit_auto=_has_any(
                normalized, ("elige automaticamente", "choose automatically", "automatic routing")
            ),
            deep_research=_has_any(
                normalized, ("deep research", "investigacion profunda", "investiga a fondo")
            ),
        )

    def rule_classification(self, text: str) -> Classification:
        normalized = normalize_text(text)
        signals = self.signals(text)
        current = _has_any(
            normalized,
            (
                "actual", "reciente", "latest", "current", "hoy", "today", "esta semana",
                "this week", "2026", "ahora", "now",
            )
        ) or _has_pattern(normalized, r"actual(?:es)?")
        citations = _has_any(
            normalized, ("fuentes", "citas", "sources", "citations", "referencias", "links")
        )
        high_risk = _has_any(
            normalized,
            (
                "produccion", "production", "seguridad", "security", "destructiv", "migracion",
                "migration", "incidente", "incident", "outage", "caido", "breach",
            )
        ) or "destructiv" in normalized
        high_scope = _has_any(
            normalized,
            (
                "multiarchivo", "multi-file", "arquitectura", "architecture", "sistema completo",
                "cross-system", "refactor grande", "large refactor", "critico", "critical",
            )
        )
        medium_scope = high_scope or _has_any(
            normalized, ("varios archivos", "multiple files", "implementa", "implement", "compara", "compare")
        ) or _has_pattern(normalized, r"implementa+r|implement+")

        task_class = "chat"
        if signals.explicit_local_large:
            task_class = "local_large"
        elif signals.deep_research:
            task_class = "deep_research"
        elif _has_any(normalized, ("incidente", "incident", "produccion caida", "production down")) or _has_pattern(normalized, r"outage+"):
            task_class = "incident"
        elif _has_any(normalized, ("revisa", "audita", "audit", "code review")) or _has_pattern(normalized, r"review+"):
            task_class = "review"
        elif _has_any(
            normalized,
            (
                "codigo", "code", "bug", "debug", "implementa", "implement", "refactor", "test",
                "corrige", "fix", "typo", "funcion", "function", "api endpoint",
            )
        ) or _has_pattern(normalized, r"implementa+r|implement+"):
            task_class = "coding"
        elif _has_any(normalized, ("investiga", "analiza fuentes", "compare sources")) or _has_pattern(normalized, r"investiga+r|research+"):
            task_class = "research"
        elif current or citations or _has_any(normalized, ("busca", "lookup", "find out", "precio")) or _has_pattern(normalized, r"weather+"):
            task_class = "lookup"

        if task_class in {"deep_research", "incident"} or high_scope or high_risk:
            complexity = "high"
        elif medium_scope or (task_class == "research" and (current or citations)):
            complexity = "medium"
        else:
            complexity = "low"

        risk = "high" if high_risk else ("medium" if task_class in {"coding", "review"} and medium_scope else "low")
        tools: list[str] = []
        if task_class in {"lookup", "research", "deep_research"}:
            tools.extend(["web_search", "web_extract"])
        if citations or task_class in {"research", "deep_research"}:
            tools.append("citations")
        if task_class == "deep_research":
            tools.append("deep_research")
        if task_class in {"coding", "review", "incident"}:
            tools.extend(["files", "terminal"])
        if task_class in {"coding", "review"}:
            tools.append("tests")
        if signals.no_tools:
            tools = []

        route = self._route_for(task_class, complexity)
        if signals.explicit_profile:
            route = signals.explicit_profile
        confidence = 0.94 if signals.explicit_profile or signals.local_only or signals.deep_research else 0.78
        if task_class == "chat":
            confidence = 0.88
        return Classification(
            task_class=task_class,
            complexity=complexity,
            needs_current_data=current,
            needs_tools=tuple(dict.fromkeys(tools)),
            privacy="local_only" if signals.local_only else "cloud_allowed",
            risk=risk,
            route=route,
            confidence=confidence,
            reason="deterministic request signals",
        )

    def decide(self, text: str, semantic: Classification | None = None, mode: str = "shadow") -> Decision:
        signals = self.signals(text)
        baseline = self.rule_classification(text)
        classification = self._merge_semantic(baseline, semantic, signals)
        proposed = classification.route
        explicit = bool(signals.explicit_profile or signals.explicit_local_large)

        if signals.local_only or classification.privacy == "local_only":
            final = "local_large" if signals.explicit_local_large else "local"
            rule = "privacy_local_only"
        elif signals.explicit_profile:
            final = signals.explicit_profile
            rule = "explicit_profile"
        elif signals.explicit_local_large:
            final = "local_large"
            rule = "explicit_local_large"
        elif classification.confidence >= self.apply_confidence:
            final = classification.route
            rule = "semantic_high_confidence" if semantic else "deterministic_high_confidence"
        elif classification.confidence >= self.economical_confidence:
            final = str(self.economical_routes.get(classification.route, classification.route))
            rule = "economical_route"
        else:
            final = "local"
            rule = "low_confidence_fallback"

        if final not in self.allowed_routes:
            final = "local"
            rule = "invalid_route_fallback"
        delegate = final in self.profiles and (mode == "auto" or (mode == "explicit" and explicit))
        if mode not in {"shadow", "explicit", "auto"}:
            delegate = False
            rule = "disabled"
        classification = replace(classification, route=proposed)
        return Decision(classification, proposed, final, rule, explicit, delegate)

    def _merge_semantic(
        self, baseline: Classification, semantic: Classification | None, signals: Signals
    ) -> Classification:
        if semantic is None:
            return baseline
        order = {"low": 0, "medium": 1, "high": 2}
        task_class = semantic.task_class
        if task_class == "local_large" and not signals.explicit_local_large:
            task_class = baseline.task_class
        complexity = max((baseline.complexity, semantic.complexity), key=order.__getitem__)
        risk = max((baseline.risk, semantic.risk), key=order.__getitem__)
        privacy = "local_only" if "local_only" in {baseline.privacy, semantic.privacy} else "cloud_allowed"
        tools = tuple(dict.fromkeys((*baseline.needs_tools, *semantic.needs_tools)))
        if signals.no_tools:
            tools = ()
        route = self._route_for(task_class, complexity)
        return Classification(
            task_class=task_class,
            complexity=complexity,
            needs_current_data=baseline.needs_current_data or semantic.needs_current_data,
            needs_tools=tools,
            privacy=privacy,
            risk=risk,
            route=route,
            confidence=semantic.confidence,
            reason=semantic.reason,
        )

    def budget_for(self, route: str) -> dict[str, Any]:
        value = self.budgets.get(route, {})
        return dict(value) if isinstance(value, dict) else {}

    def _route_for(self, task_class: str, complexity: str) -> str:
        task_routes = self.routes.get(task_class, {})
        route = task_routes.get(complexity, "local") if isinstance(task_routes, dict) else "local"
        return str(route) if str(route) in self.allowed_routes else "local"
