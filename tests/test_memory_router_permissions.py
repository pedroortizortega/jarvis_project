import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "hermes-native" / "memory-router" / "src")
)

from memory_router.permissions import (
    AuthorizationError,
    IDENTITY_ROLES,
    ROLES,
    authorize,
)


class PermissionsTests(unittest.TestCase):
    def test_role_set_is_exactly_three(self):
        self.assertEqual({"coder", "scientist", "jarvis"}, set(ROLES))

    def test_unknown_role_rejected(self):
        with self.assertRaises(AuthorizationError):
            authorize(
                role="admin",
                identity_name="codex",
                namespace="/global",
                verb="search",
            )

    def test_role_outside_clients_permitted_set_rejected(self):
        # codex is only permitted the "coder" role.
        with self.assertRaises(AuthorizationError):
            authorize(
                role="jarvis",
                identity_name="codex",
                namespace="/global",
                verb="store",
            )

    def test_client_acting_within_permitted_role_succeeds(self):
        authorize(
            role="coder",
            identity_name="codex",
            namespace="/projects/lector-ine",
            verb="store",
        )

    def test_coder_denied_admin_namespace(self):
        with self.assertRaises(AuthorizationError):
            authorize(
                role="coder",
                identity_name="codex",
                namespace="admin/settings",
                verb="search",
            )

    def test_coder_denied_user_master(self):
        with self.assertRaises(AuthorizationError):
            authorize(
                role="coder",
                identity_name="codex",
                namespace="/user/master",
                verb="search",
            )

    def test_coder_may_only_search_global(self):
        authorize(
            role="coder", identity_name="codex", namespace="/global", verb="search"
        )
        with self.assertRaises(AuthorizationError):
            authorize(
                role="coder", identity_name="codex", namespace="/global", verb="store"
            )

    def test_coder_denied_other_agent_namespace(self):
        with self.assertRaises(AuthorizationError):
            authorize(
                role="coder",
                identity_name="codex",
                namespace="/agents/jarvis",
                verb="search",
            )

    def test_coder_allowed_own_agent_namespace(self):
        authorize(
            role="coder",
            identity_name="codex",
            namespace="/agents/codex",
            verb="store",
        )

    def test_scientist_store_and_search_global(self):
        authorize(
            role="scientist",
            identity_name="opencode",
            namespace="/global",
            verb="store",
        )
        authorize(
            role="scientist",
            identity_name="opencode",
            namespace="/global",
            verb="search",
        )

    def test_scientist_may_only_search_projects(self):
        authorize(
            role="scientist",
            identity_name="opencode",
            namespace="/projects/lector-ine",
            verb="search",
        )
        with self.assertRaises(AuthorizationError):
            authorize(
                role="scientist",
                identity_name="opencode",
                namespace="/projects/lector-ine",
                verb="store",
            )

    def test_jarvis_full_access_except_admin(self):
        for namespace, verb in [
            ("/global", "store"),
            ("/user/master", "store"),
            ("/projects/lector-ine", "store"),
            ("/agents/jarvis", "store"),
            ("/agents/codex", "store"),
        ]:
            authorize(
                role="jarvis",
                identity_name="hermes-gateway",
                namespace=namespace,
                verb=verb,
            )
        with self.assertRaises(AuthorizationError):
            authorize(
                role="jarvis",
                identity_name="hermes-gateway",
                namespace="admin/settings",
                verb="search",
            )

    def test_identity_roles_mapping_is_server_side(self):
        self.assertEqual({"coder"}, IDENTITY_ROLES["pedro-claude-code"])
        self.assertEqual({"coder"}, IDENTITY_ROLES["codex"])
        self.assertEqual({"coder", "scientist"}, IDENTITY_ROLES["opencode"])
        self.assertEqual({"jarvis"}, IDENTITY_ROLES["hermes-gateway"])


class ReflectPermissionsTests(unittest.TestCase):
    def test_jarvis_allowed_reflect_on_user_master(self):
        authorize(
            role="jarvis",
            identity_name="hermes-gateway",
            namespace="/user/master",
            verb="reflect",
        )

    def test_scientist_allowed_reflect_on_user_master(self):
        authorize(
            role="scientist",
            identity_name="opencode",
            namespace="/user/master",
            verb="reflect",
        )

    def test_coder_denied_reflect_on_user_master(self):
        with self.assertRaises(AuthorizationError):
            authorize(
                role="coder",
                identity_name="codex",
                namespace="/user/master",
                verb="reflect",
            )

    def test_reflect_denied_on_every_other_namespace_kind_for_all_roles(self):
        cases = [
            ("jarvis", "hermes-gateway", "/global"),
            ("jarvis", "hermes-gateway", "/projects/lector-ine"),
            ("jarvis", "hermes-gateway", "/agents/hermes-gateway"),
            ("jarvis", "hermes-gateway", "/agents/codex"),
            ("scientist", "opencode", "/global"),
            ("scientist", "opencode", "/projects/lector-ine"),
            ("scientist", "opencode", "/agents/opencode"),
            ("scientist", "opencode", "/agents/codex"),
            ("coder", "codex", "/global"),
            ("coder", "codex", "/projects/lector-ine"),
            ("coder", "codex", "/agents/codex"),
            ("coder", "codex", "/agents/hermes-gateway"),
        ]
        for role, identity_name, namespace in cases:
            with self.subTest(role=role, namespace=namespace):
                with self.assertRaises(AuthorizationError):
                    authorize(
                        role=role,
                        identity_name=identity_name,
                        namespace=namespace,
                        verb="reflect",
                    )


if __name__ == "__main__":
    unittest.main()
