# gpu-handoff-orchestration Specification

## Purpose

An ordered, guarded sequence that releases the single GPU for Cloud mode and
reclaims it for Local mode, safely and recoverably.

## Requirements

### Requirement: Ordered Switch-to-Cloud Sequence
When switching to Cloud, the system MUST perform, in order: (1) drain the
in-flight local request, (2) pause KEDA ScaledObjects, (3) scale
vLLM/llama.cpp deployments to 0, (4) wait for pod deletion, (5) confirm GPU is
free, (6) update LiteLLM routing and Hermes `model.default` to Cloud.

#### Scenario: Successful switch to Cloud
- GIVEN mode = Local with no stuck pods
- WHEN the user triggers switch to Cloud
- THEN the system executes all six steps in order
- AND ends with mode = Cloud and 0 GPU inference pods running

#### Scenario: In-flight request is drained, not cut
- GIVEN a request is currently being served by the local model
- WHEN the user triggers switch to Cloud
- THEN the system MUST wait for that request to complete before scaling vLLM/llama.cpp to 0

### Requirement: Ordered Switch-to-Local Sequence
When switching to Local, the system MUST perform, in order: (1) scale up the
fixed default local profile deployment, (2) wait for readiness, (3) resume
KEDA ScaledObjects, (4) update LiteLLM routing and Hermes `model.default` to
Local.

#### Scenario: Successful switch to Local
- GIVEN mode = Cloud
- WHEN the user triggers switch to Local
- THEN the system brings up the fixed default profile, not the profile that was active before the prior switch to Cloud

#### Scenario: Return-to-Local ignores previous profile
- GIVEN the local profile was "large" before switching to Cloud
- WHEN the user switches back to Local
- THEN the resulting active profile MUST be the fixed default profile (e.g. daily), not "large"

### Requirement: GPU Confirmation Timeout Blocks Switch
If the GPU is not confirmed free within the expected window during a
switch-to-Cloud, the system MUST warn, MUST NOT complete the switch, and MUST
leave the system in the last known-consistent state.

#### Scenario: Pod stuck terminating
- GIVEN a vLLM/llama.cpp pod is stuck in Terminating state past the expected window
- WHEN the GPU-free confirmation step runs
- THEN the system surfaces a clear error, does not update LiteLLM/Hermes routing, and does not force the switch

#### Scenario: Recovery after timeout
- GIVEN a switch to Cloud was blocked by a GPU confirmation timeout
- WHEN the underlying pod deletion later completes
- THEN a retry of the switch MUST be able to proceed cleanly from the last known-consistent state

### Requirement: Exclusive GPU Occupancy
The system MUST NOT allow vLLM and llama.cpp to be scaled up simultaneously
at any point in the sequence.

#### Scenario: Guard against concurrent scale-up
- GIVEN vLLM is scaled to 0 as part of a switch to Cloud
- WHEN any subsequent step runs
- THEN llama.cpp MUST NOT be scaled above 0 while vLLM/Cloud is active, and vice versa

### Requirement: No Regression to switch-model.sh
The orchestration logic MUST reuse/generalize the guarded sequence from
`switch-model.sh` without changing its existing daily↔large CLI behavior.

#### Scenario: CLI daily↔large switch unaffected
- GIVEN the panel and orchestration service are deployed
- WHEN `switch-model.sh` is invoked directly from the CLI to switch daily↔large
- THEN its behavior MUST be identical to before this change
