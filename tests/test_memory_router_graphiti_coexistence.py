"""graphiti-backend Unit 2: zero-diff verification (Phase 8) and matrix
closure (Phase 10.1). Kept in its own module rather than
`test_memory_router_graphiti_adapter.py`, which already shipped and merged
as Unit 1 (PR 1) — this module belongs to Unit 2 (PR 2)."""

import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(
    0, str(REPO_ROOT / "hermes-native" / "memory-router" / "src")
)

from memory_router.backends.cognee import CogneeBackend
from memory_router.backends.engram import EngramBackend
from memory_router.backends.graphiti import GraphitiBackend
from memory_router.backends.hindsight import HindsightBackend
from memory_router.backends.honcho import HonchoBackend
from memory_router.registry import Registry


class ZeroDiffVerificationTests(unittest.TestCase):
    """Phase 8: contracts.py, app.py, and registry.py MUST have zero diff
    against origin/main. F-1 (app.py's existing `empty` status mapping) and
    F-3 (registry.py's fnmatch-based selection) already serve Graphiti with
    no code change (design.md Verified Findings)."""

    _TRACKED_FILES = (
        "hermes-native/memory-router/src/memory_router/contracts.py",
        "hermes-native/memory-router/src/memory_router/app.py",
        "hermes-native/memory-router/src/memory_router/registry.py",
    )

    def test_contracts_app_registry_have_zero_diff_against_origin_main(self):
        result = subprocess.run(
            [
                "git",
                "diff",
                "--stat",
                "origin/main",
                "--",
                *self._TRACKED_FILES,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            "",
            result.stdout.strip(),
            f"expected zero diff, got:\n{result.stdout}",
        )

    def test_app_empty_status_mapping_already_present_no_change_needed(self):
        # F-1: app.py's Dispatcher.reflect() already maps a backend result
        # of status="empty" onto the response's "empty" status when nothing
        # higher-precedence (ready/pending) has been set. Confirmed by
        # source inspection rather than re-implemented — this test locks
        # the finding, not the behavior (already covered end-to-end by
        # cognee-backend's dispatcher tests).
        app_source = (
            REPO_ROOT
            / "hermes-native"
            / "memory-router"
            / "src"
            / "memory_router"
            / "app.py"
        ).read_text()
        self.assertIn('elif result.status == "empty"', app_source)

    def test_registry_fnmatch_selection_already_generic_no_change_needed(self):
        # F-3: registry.backends_for gates purely on capabilities() via
        # fnmatch, never isinstance, so /agents/foo matches /agents/* with
        # no registry.py code change.
        registry_source = (
            REPO_ROOT
            / "hermes-native"
            / "memory-router"
            / "src"
            / "memory_router"
            / "registry.py"
        ).read_text()
        self.assertIn("fnmatch", registry_source)
        self.assertNotIn("isinstance", registry_source)


class MatrixClosureTests(unittest.TestCase):
    """Phase 10.1: every fixed namespace root now has at least one
    reflect-capable backend under the full registry."""

    def test_every_fixed_root_has_a_reflect_capable_backend(self):
        registry = Registry(
            backends=[
                EngramBackend(),
                HindsightBackend(base_url="http://h1"),
                HonchoBackend(base_url="http://h2"),
                CogneeBackend(base_url="http://c"),
                GraphitiBackend(base_url="http://g"),
            ]
        )
        fixed_roots = ["/user/master", "/projects/x", "/global", "/agents/x"]
        for namespace in fixed_roots:
            with self.subTest(namespace=namespace):
                selected = registry.backends_for(verb="reflect", namespace=namespace)
                self.assertGreaterEqual(
                    len(selected),
                    1,
                    f"expected at least one reflect-capable backend for {namespace!r}",
                )


if __name__ == "__main__":
    unittest.main()
