from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from hermes_intent_orchestration.policy import Classification, RouterPolicy


ROOT = Path(__file__).resolve().parents[1]


class RouterPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = RouterPolicy.from_path(ROOT / "policy.yaml")

    def test_evaluation_corpus_meets_accuracy_and_privacy_contract(self) -> None:
        corpus = yaml.safe_load((ROOT / "evaluation-cases.yaml").read_text(encoding="utf-8"))
        cases = corpus["cases"]
        self.assertGreaterEqual(len(cases), 100)
        correct = 0
        for case in cases:
            decision = self.policy.decide(case["input"], mode="auto")
            correct += decision.final_route == case["expected_route"]
            if case["id"].startswith("local-only-"):
                self.assertIn(decision.final_route, {"local", "local_large"}, case["id"])
                self.assertFalse(decision.should_delegate, case["id"])
        self.assertGreaterEqual(correct / len(cases), 0.90)

    def test_privacy_overrides_explicit_cloud_profile(self) -> None:
        decision = self.policy.decide("Solo local: usa sol-high con mis credenciales", mode="auto")
        self.assertEqual(decision.final_route, "local")
        self.assertEqual(decision.rule, "privacy_local_only")
        self.assertFalse(decision.should_delegate)

    def test_sensitive_data_markers_block_explicit_cloud_profiles(self) -> None:
        prompts = (
            "Use sol-high to inspect this confidential customer database",
            "Use terra-medium to process this SSN list",
            "Use luna-low to debug AWS_ACCESS_KEY_ID handling with the real value",
        )
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                decision = self.policy.decide(prompt, mode="explicit")
                self.assertEqual(decision.final_route, "local")
                self.assertFalse(decision.should_delegate)

    def test_profile_mentions_are_not_overrides(self) -> None:
        decision = self.policy.decide("Compare luna-low and luna-high", mode="auto")
        self.assertFalse(decision.explicit_override)

    def test_spaced_profile_override_is_canonicalized(self) -> None:
        decision = self.policy.decide("Usa Sol High para esta tarea", mode="explicit")
        self.assertEqual(decision.final_route, "sol-high")
        self.assertTrue(decision.should_delegate)

    def test_semantic_route_cannot_select_profile_directly(self) -> None:
        semantic = Classification(
            task_class="chat",
            complexity="low",
            needs_current_data=False,
            needs_tools=(),
            privacy="cloud_allowed",
            risk="low",
            route="sol-high",
            confidence=1.0,
            reason="untrusted recommendation",
        )
        decision = self.policy.decide("Explain gravity", semantic=semantic, mode="auto")
        self.assertEqual(decision.final_route, "local")

    def test_semantic_privacy_is_enforced(self) -> None:
        semantic = Classification(
            task_class="research",
            complexity="high",
            needs_current_data=True,
            needs_tools=("web_search",),
            privacy="local_only",
            risk="high",
            route="sol-high",
            confidence=1.0,
            reason="private material",
        )
        decision = self.policy.decide("Analyze the attached material", semantic=semantic, mode="auto")
        self.assertEqual(decision.final_route, "local")
        self.assertFalse(decision.should_delegate)

    def test_no_tools_is_a_hard_constraint(self) -> None:
        semantic = Classification(
            task_class="research",
            complexity="medium",
            needs_current_data=True,
            needs_tools=("web_search", "citations"),
            privacy="cloud_allowed",
            risk="low",
            route="terra-medium",
            confidence=0.9,
            reason="research",
        )
        decision = self.policy.decide("Research this without tools", semantic=semantic, mode="auto")
        self.assertEqual(decision.classification.needs_tools, ())


if __name__ == "__main__":
    unittest.main()
