# model-switch-panel Specification

## Purpose

A minimal LAN-only web page that lets a human see and change whether inference
runs Local (GPU) or Cloud (Codex/ChatGPT OAuth session via codex-shim),
without a terminal.

## Requirements

### Requirement: Current-State View
The system MUST display the current inference mode (Local or Cloud), the
active local profile when Local, and any in-progress or failed switch state.

#### Scenario: Steady-state Local
- GIVEN no switch is in progress
- WHEN a user loads the panel
- THEN the panel shows mode = Local and the active profile (daily or large)

#### Scenario: Partial/failed state surfaced
- GIVEN a previous switch attempt left the system in a partial state
- WHEN a user loads the panel
- THEN the panel shows the true partial state and a clear error, not a stale "Local" or "Cloud" label

### Requirement: Local/Cloud Toggle
The system MUST provide one action to switch between Local and Cloud modes.

#### Scenario: Switch to Cloud
- GIVEN the panel shows mode = Local
- WHEN the user triggers the Cloud switch
- THEN the panel shows switch progress and, on success, mode = Cloud

#### Scenario: Switch to Local
- GIVEN the panel shows mode = Cloud
- WHEN the user triggers the Local switch
- THEN the panel shows switch progress and, on success, mode = Local with the fixed default profile active

#### Scenario: Toggle disabled during in-progress switch
- GIVEN a switch is currently in progress
- WHEN the user attempts to trigger another switch
- THEN the system MUST reject the new action and keep the toggle disabled until the in-progress switch resolves

### Requirement: Local Profile Picker
The system MUST let the user pick the local profile (daily or large) while in
Local mode, without affecting the existing `switch-model.sh` CLI behavior.

The switch MUST be performed entirely from inside the cluster, using the
`llama-router`'s request-triggered autoload plus the stable LiteLLM alias
rewrite. The system MUST NOT depend on the host `hermes` CLI, `sudo`, or any
host-side agent.

#### Scenario: Change profile while Local
- GIVEN mode = Local with profile = daily
- WHEN the user selects profile = large
- THEN the system drains in-flight router requests, loads the large preset on `llama-router` and waits for that load to complete, repoints the stable LiteLLM alias at the large preset, restarts LiteLLM, and only then reports profile = large in the state view

#### Scenario: Target profile is loaded before traffic is repointed
- GIVEN a profile switch is requested
- WHEN the system performs the switch
- THEN the target model MUST be fully loaded on the router BEFORE the LiteLLM alias is repointed, so that no client request is left waiting on a cold model load

#### Scenario: Profile picker unavailable in Cloud mode
- GIVEN mode = Cloud
- WHEN the user views the panel
- THEN the profile picker MUST be disabled or hidden, since no local profile is active

#### Scenario: Profile change rejected while not Local
- GIVEN mode = Cloud, or a switch is already in progress
- WHEN a profile change is requested
- THEN the system MUST reject it and perform zero cluster mutations

#### Scenario: Failed profile switch restores routing
- GIVEN a profile switch fails partway
- WHEN the system aborts
- THEN the previous LiteLLM alias configuration MUST be restored and the state view MUST show the failure, never a silently wrong profile

### Requirement: Codex Session-Status Indicator
The system MUST show the Codex/ChatGPT session status alongside the toggle,
as one of: "not configured", "valid", "expiring soon", "expired / needs
re-login", "refresh failed". The system MUST NOT show a spend or
quota-remaining number, since no such endpoint is verified for this
credential.

#### Scenario: Status shown regardless of mode
- GIVEN the panel is loaded
- WHEN the codex-shim reports a session status
- THEN the panel displays that exact status next to the toggle

#### Scenario: Non-valid status blocks switch to Cloud
- GIVEN session status is "not configured", "expired / needs re-login", or "refresh failed"
- WHEN the user views or attempts the switch to Cloud
- THEN the panel disables/blocks the switch-to-Cloud action and shows the reason instead of attempting or silently degrading it

### Requirement: LAN-Only Exposure
The system MUST be reachable only via LAN Ingress with mTLS, matching the
Engram precedent, and MUST NOT be exposed via LoadBalancer or NodePort.

#### Scenario: Access without client certificate
- GIVEN a client without a valid mTLS certificate
- WHEN it attempts to reach the panel
- THEN the request MUST be rejected before reaching the application
