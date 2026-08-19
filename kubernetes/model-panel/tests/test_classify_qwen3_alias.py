"""Regression tests for a bug found live (Amendment 5): a routine `kubectl
apply -f litellm-config.yaml` silently reverts the `qwen3` alias to the
file's checked-in baseline (historically `vllm.llms.svc.cluster.local`),
undoing whatever the panel last live-patched into the ConfigMap. Hermes then
gets connection errors in "cloud" mode with no panel-visible signal.
`classify_qwen3_alias_target` is the read-only building block for detecting
this."""

from __future__ import annotations

from app.handoff.steps import classify_qwen3_alias_target

CLOUD_YAML = """
model_list:
  - model_name: qwen3
    litellm_params:
      model: openai/gpt-5.6-sol
      api_base: http://codex-shim.llms.svc.cluster.local:8080/v1
      api_key: os.environ/CODEX_SHIM_KEY
"""

LOCAL_YAML = """
model_list:
  - model_name: qwen3
    litellm_params:
      model: openai/qwen3.5-9b
      api_base: http://llama-router.llms.svc.cluster.local:8080/v1
      api_key: os.environ/LLAMA_API_KEY
"""

FILE_BASELINE_VLLM_YAML = """
model_list:
  - model_name: qwen3
    litellm_params:
      model: openai/qwen3
      api_base: http://vllm.llms.svc.cluster.local:8000/v1
      api_key: os.environ/LLAMA_API_KEY
"""

NO_QWEN3_ENTRY_YAML = """
model_list:
  - model_name: qwen3.6-27b
    litellm_params:
      model: openai/qwen3.6-27b-q3
      api_base: http://llama-router.llms.svc.cluster.local:8080/v1
"""


def test_classifies_codex_shim_as_cloud():
    assert classify_qwen3_alias_target(CLOUD_YAML) == "cloud"


def test_classifies_llama_router_as_local():
    assert classify_qwen3_alias_target(LOCAL_YAML) == "local"


def test_classifies_file_baseline_vllm_as_local_not_unknown():
    """The exact drift-causing case: after `kubectl apply -f` reverts to
    the file's checked-in vllm baseline, this must still register as
    "local" (not None/unknown) so reconcile_against_live's comparison
    against state.mode="cloud" correctly flags drift."""
    assert classify_qwen3_alias_target(FILE_BASELINE_VLLM_YAML) == "local"


def test_classifies_missing_qwen3_entry_as_none():
    assert classify_qwen3_alias_target(NO_QWEN3_ENTRY_YAML) is None


def test_classifies_malformed_yaml_as_none():
    assert classify_qwen3_alias_target("not: valid: yaml: [") is None


def test_classifies_empty_string_as_none():
    assert classify_qwen3_alias_target("") is None
