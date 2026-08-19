import unittest

from memory_router.identity import IdentityError, resolve_identity

CN_TO_IDENTITY = {
    "pedro-claude-code": "pedro-claude-code",
    "codex": "codex",
    "opencode": "opencode",
    "hermes-gateway": "hermes-gateway",
}

BEARER_BY_IDENTITY = {
    "pedro-claude-code": "token-pcc",
    "codex": "token-codex",
    "opencode": "token-oc",
    "hermes-gateway": "token-hg",
}


class IdentityResolutionTests(unittest.TestCase):
    def resolve(self, cn, bearer):
        return resolve_identity(
            cn,
            bearer,
            cn_to_identity=CN_TO_IDENTITY,
            bearer_by_identity=BEARER_BY_IDENTITY,
        )

    def test_resolves_known_cn_with_matching_bearer(self):
        identity = self.resolve("codex", "token-codex")
        self.assertEqual("codex", identity.name)

    def test_rejects_unknown_cn(self):
        with self.assertRaises(IdentityError):
            self.resolve("unknown-client", "token-codex")

    def test_rejects_mismatched_bearer(self):
        with self.assertRaises(IdentityError):
            self.resolve("codex", "token-pcc")

    def test_rejects_missing_bearer(self):
        with self.assertRaises(IdentityError):
            self.resolve("codex", "")
        with self.assertRaises(IdentityError):
            self.resolve("codex", None)

    def test_rejects_missing_cn(self):
        with self.assertRaises(IdentityError):
            self.resolve("", "token-codex")
        with self.assertRaises(IdentityError):
            self.resolve(None, "token-codex")


if __name__ == "__main__":
    unittest.main()
