"""Root-suite bridge for `local-embeddings` (D-15).

`openspec/config.yaml`'s `test_command` (`python -m unittest discover -s
tests`) only ever roots discovery at this package, so the pure-core and
manifest `unittest.TestCase` suites living in
`kubernetes/local-embeddings/tests/` are invisible to the enforced
strict-TDD gate unless reached across, same as
`tests/test_memory_router_registry.py:5` already does for memory-router.

`test_api.py` (FastAPI `TestClient`, pytest-only) is deliberately **not**
bridged here — pulling `fastapi`/`httpx` into the root suite is a heavier
precedent than PyYAML, and the pure-core split means every decision-bearing
line is already reachable through the two modules imported below.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "kubernetes" / "local-embeddings" / "tests")
)

from test_embeddings_core import *  # noqa: E402,F401,F403
from test_local_embeddings_manifest import *  # noqa: E402,F401,F403

if __name__ == "__main__":
    unittest.main()
