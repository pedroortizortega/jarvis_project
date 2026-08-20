import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "hermes-native" / "memory-router" / "src")
)

from memory_router.namespaces import NamespaceError, validate_namespace


class NamespaceValidationTests(unittest.TestCase):
    def test_accepts_global_root(self):
        self.assertEqual("/global", validate_namespace("/global"))

    def test_accepts_user_master_root(self):
        self.assertEqual("/user/master", validate_namespace("/user/master"))

    def test_accepts_project_namespace(self):
        self.assertEqual(
            "/projects/lector-ine", validate_namespace("/projects/lector-ine")
        )

    def test_accepts_agent_namespace(self):
        self.assertEqual("/agents/jarvis", validate_namespace("/agents/jarvis"))

    def test_rejects_missing_namespace(self):
        with self.assertRaises(NamespaceError):
            validate_namespace(None)
        with self.assertRaises(NamespaceError):
            validate_namespace("")

    def test_rejects_traversal(self):
        with self.assertRaises(NamespaceError):
            validate_namespace("/projects/../global")
        with self.assertRaises(NamespaceError):
            validate_namespace("/agents/..%2f..%2fglobal")

    def test_rejects_wildcards(self):
        with self.assertRaises(NamespaceError):
            validate_namespace("/projects/*")
        with self.assertRaises(NamespaceError):
            validate_namespace("/agents/*")

    def test_rejects_unknown_root(self):
        with self.assertRaises(NamespaceError):
            validate_namespace("/admin/settings")
        with self.assertRaises(NamespaceError):
            validate_namespace("global")

    def test_rejects_project_root_without_name(self):
        with self.assertRaises(NamespaceError):
            validate_namespace("/projects")
        with self.assertRaises(NamespaceError):
            validate_namespace("/projects/")

    def test_rejects_nested_project_namespace(self):
        # design.md F-1: /projects/a/b is unreachable — _NAME_RE has no "/"
        # in its character class, so the whole remainder ("a/b") fails to
        # match and NamespaceError is raised here, before permissions,
        # before the registry, before any adapter.
        with self.assertRaises(NamespaceError):
            validate_namespace("/projects/a/b")


if __name__ == "__main__":
    unittest.main()
