"""RED (part of 5.1/6.x): write-ahead state ConfigMap client (D6)."""
from __future__ import annotations

from app.handoff.state import HandoffState, StateStore, reconcile_against_live


def test_read_returns_default_state_when_configmap_missing(fake_core_v1):
    store = StateStore(core_v1=fake_core_v1)
    state = store.read()
    assert state.mode == "local"
    assert state.phase == "idle"


def test_write_then_read_round_trips(fake_core_v1):
    store = StateStore(core_v1=fake_core_v1, clock=lambda: 1000.0)
    written = store.write(HandoffState(mode="cloud", profile=None, phase="idle"))
    assert written.updated_at == 1000.0

    read_back = store.read()
    assert read_back.mode == "cloud"
    assert read_back.profile is None
    assert read_back.phase == "idle"
    assert read_back.updated_at == 1000.0


def test_write_is_write_ahead_before_mutation_semantics(fake_core_v1):
    """The state write itself must be a plain, cheap ConfigMap patch — callers
    are responsible for calling write() *before* starting a mutating step."""
    store = StateStore(core_v1=fake_core_v1)
    store.write(HandoffState(mode="local", phase="transitioning", target="cloud"))
    state = store.read()
    assert state.phase == "transitioning"
    assert state.target == "cloud"


def test_reconcile_against_live_reports_no_drift_while_transitioning():
    state = HandoffState(mode="local", phase="transitioning")
    result = reconcile_against_live(state, router_replicas=0, gpu_pods_present=False)
    assert result == {"drift": False, "consistent": True}


def test_reconcile_against_live_detects_drift():
    # Claim says local (router should be up, GPU pods present) but live
    # cluster shows router scaled to 0 -> drift.
    state = HandoffState(mode="local", phase="idle")
    result = reconcile_against_live(state, router_replicas=0, gpu_pods_present=False)
    assert result["drift"] is True
    assert result["consistent"] is False


def test_reconcile_against_live_consistent_when_matching():
    state = HandoffState(mode="cloud", phase="idle")
    result = reconcile_against_live(state, router_replicas=0, gpu_pods_present=False)
    assert result == {"drift": False, "consistent": True}


def test_reconcile_against_live_detects_alias_drift_even_when_gpu_matches():
    """Regression test found live (Amendment 5): a routine `kubectl apply -f
    litellm-config.yaml` reverts the qwen3 alias to its file baseline
    without touching router replicas or GPU pods — router/GPU checks alone
    stay consistent, so the alias check must be independent, not folded
    into them."""
    state = HandoffState(mode="cloud", phase="idle")
    result = reconcile_against_live(
        state, router_replicas=0, gpu_pods_present=False, qwen3_alias_target="local"
    )
    assert result["drift"] is True
    assert result["alias_drift"] is True


def test_reconcile_against_live_no_alias_drift_when_matching():
    state = HandoffState(mode="cloud", phase="idle")
    result = reconcile_against_live(
        state, router_replicas=0, gpu_pods_present=False, qwen3_alias_target="cloud"
    )
    assert result == {"drift": False, "consistent": True, "alias_drift": False}


def test_reconcile_against_live_skips_alias_check_when_target_omitted():
    state = HandoffState(mode="cloud", phase="idle")
    result = reconcile_against_live(state, router_replicas=0, gpu_pods_present=False)
    assert "alias_drift" not in result
