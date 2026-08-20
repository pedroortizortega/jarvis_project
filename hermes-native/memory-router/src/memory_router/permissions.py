ROLES = frozenset({"coder", "scientist", "jarvis"})

# Server-side, router-owned mapping of onboarded client identity -> permitted
# role(s). A caller only *declares* which of its permitted roles it acts as
# for a given request; it can never self-assert a role outside this set.
IDENTITY_ROLES: dict[str, frozenset[str]] = {
    "pedro-claude-code": frozenset({"coder"}),
    "codex": frozenset({"coder"}),
    "opencode": frozenset({"coder", "scientist"}),
    "hermes-gateway": frozenset({"jarvis"}),
}


class AuthorizationError(ValueError):
    """Raised when a request is denied by role, identity, or namespace+verb
    authorization. Deny-by-default: anything not explicitly allowed is
    denied."""


def _namespace_kind(namespace: str, identity_name: str) -> str:
    if namespace == "/global":
        return "global"
    if namespace == "/user/master":
        return "user_master"
    if namespace.startswith("/projects/"):
        return "projects"
    if namespace.startswith("/agents/"):
        agent_name = namespace[len("/agents/"):]
        return "agents_self" if agent_name == identity_name else "agents_other"
    return "other"


# Phase 1 role table (design.md "Auth & Permissions"): role -> namespace kind
# -> allowed verbs. Anything absent from this table is denied.
_ROLE_TABLE: dict[str, dict[str, frozenset[str]]] = {
    "coder": {
        "global": frozenset({"search"}),
        "user_master": frozenset(),
        "projects": frozenset({"store", "search"}),
        "agents_self": frozenset({"store", "search"}),
        "agents_other": frozenset(),
    },
    "scientist": {
        "global": frozenset({"store", "search"}),
        "user_master": frozenset({"search", "reflect"}),
        "projects": frozenset({"search", "reflect"}),
        "agents_self": frozenset({"store", "search"}),
        "agents_other": frozenset(),
    },
    "jarvis": {
        "global": frozenset({"store", "search"}),
        "user_master": frozenset({"store", "search", "reflect"}),
        "projects": frozenset({"store", "search", "reflect"}),
        "agents_self": frozenset({"store", "search"}),
        "agents_other": frozenset({"store", "search"}),
    },
}


def authorize(*, role: str, identity_name: str, namespace: str, verb: str) -> None:
    """Authorize a request. Raises AuthorizationError and denies by default
    unless every check (known role, role permitted for this identity,
    namespace+verb allow-rule) explicitly passes.
    """
    if role not in ROLES:
        raise AuthorizationError(f"unknown role: {role!r}")

    permitted_roles = IDENTITY_ROLES.get(identity_name, frozenset())
    if role not in permitted_roles:
        raise AuthorizationError(
            f"role {role!r} not permitted for client identity {identity_name!r}"
        )

    kind = _namespace_kind(namespace, identity_name)
    allowed_verbs = _ROLE_TABLE.get(role, {}).get(kind, frozenset())
    if verb not in allowed_verbs:
        raise AuthorizationError(
            f"role {role!r} denied verb {verb!r} on namespace {namespace!r}"
        )
