import hmac
from dataclasses import dataclass


class IdentityError(ValueError):
    """Raised when the client certificate CN or bearer token cannot be resolved
    to a known, authenticated identity. Fail-closed."""
@dataclass(frozen=True)
class Identity:
    name: str


def resolve_identity(
    cn: str | None,
    bearer: str | None,
    *,
    cn_to_identity: dict[str, str],
    bearer_by_identity: dict[str, str],
) -> Identity:
    """Resolve the Traefik-forwarded client-certificate CN to an onboarded
    identity, then verify its per-identity bearer token.

    Deny-by-default: any missing/unknown CN or non-matching bearer is
    rejected explicitly, never silently accepted.
    """
    if not cn:
        raise IdentityError("missing client certificate CN")

    identity_name = cn_to_identity.get(cn)
    if identity_name is None:
        raise IdentityError(f"unknown client identity for CN: {cn!r}")

    if not bearer:
        raise IdentityError(f"missing bearer token for identity: {identity_name!r}")

    expected_bearer = bearer_by_identity.get(identity_name)
    if not expected_bearer or not hmac.compare_digest(bearer, expected_bearer):
        raise IdentityError(f"bearer token mismatch for identity: {identity_name!r}")

    return Identity(name=identity_name)
