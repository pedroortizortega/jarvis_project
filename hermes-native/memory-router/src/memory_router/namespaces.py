import re

_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


class NamespaceError(ValueError):
    """Raised when a caller-declared namespace is invalid. Fail-closed."""
class NamespaceRoot:
    GLOBAL = "/global"
    USER_MASTER = "/user/master"
    PROJECTS = "/projects"
    AGENTS = "/agents"


def _reject_if_unsafe(namespace: str) -> None:
    if ".." in namespace or "%2f" in namespace.lower() or "%2e" in namespace.lower():
        raise NamespaceError(f"namespace traversal rejected: {namespace!r}")
    if "*" in namespace or "?" in namespace:
        raise NamespaceError(f"namespace wildcards rejected: {namespace!r}")


def validate_namespace(namespace: str | None) -> str:
    """Validate a caller-declared namespace against the four fixed roots.

    Fail-closed: any namespace that is missing, malformed, a traversal
    attempt, a wildcard, or outside the fixed roots is rejected.
    """
    if not namespace:
        raise NamespaceError("namespace is required and must be declared explicitly")
    if not namespace.startswith("/"):
        raise NamespaceError(f"namespace must be absolute: {namespace!r}")

    _reject_if_unsafe(namespace)

    if namespace == NamespaceRoot.GLOBAL:
        return namespace
    if namespace == NamespaceRoot.USER_MASTER:
        return namespace

    for root in (NamespaceRoot.PROJECTS, NamespaceRoot.AGENTS):
        prefix = root + "/"
        if namespace.startswith(prefix):
            name = namespace[len(prefix):]
            if not name or not _NAME_RE.match(name):
                raise NamespaceError(f"invalid name for {root} namespace: {namespace!r}")
            return namespace

    raise NamespaceError(f"unknown namespace root: {namespace!r}")
