# Hermes Intent Orchestration

Standalone Hermes plugin for classifying user turns locally and routing bounded
tasks to isolated Luna, Terra, or Sol profiles. It does not modify Hermes core,
change the primary profile, copy credentials, or activate Qwen 27B.

## Modes

- `disabled`: no classification or routing.
- `shadow`: classify and audit, then use the primary unchanged.
- `explicit`: delegate only when the user explicitly names an allowlisted profile.
- `auto`: apply policy routes. High-risk and Sol routes remain local unless
  `allow_high_risk_auto` is explicitly enabled.

Start with `shadow`. The plugin only runs the semantic classifier when the
primary model's exact base URL appears in `local_base_urls`. Classification is
a single direct request to that URL with a hard timeout; it does not use the
Hermes auxiliary fallback chain.

## Install

Use the same Python environment as the system gateway:

```bash
/home/pedro/.hermes/hermes-agent/venv/bin/pip install -e \
  /home/pedro/Documentos/Projects/jarvis_project/hermes-native/orchestration
```

Add the plugin to `plugins.enabled` and configure it in `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - intent-orchestration
  entries:
    intent-orchestration:
      mode: shadow
      semantic_classifier: true
      classifier_timeout_seconds: 15
      local_base_urls:
        - http://192.168.1.241:4000/v1
      worker_cwd: /home/pedro/Documentos/Projects
      audit_enabled: true
      allow_high_risk_auto: false
      allow_terminal_workers: false
      require_classifier_for_explicit: true
```

Restart with `sudo systemctl restart hermes-gateway.service`. Roll back by
removing `intent-orchestration` from `plugins.enabled` and restarting.

## Verification

```bash
PYTHONPATH=src /home/pedro/.hermes/hermes-agent/venv/bin/python \
  -m unittest discover -s tests -v
hermes plugins list --plain --no-bundled
```

Routing metadata is stored in
`~/.hermes/orchestration/events.sqlite3`. The schema contains no prompt,
response, secret, tool output, or complete conversation fields.

Profile workers receive only policy-selected toolsets, a minimal process
environment, and `--ignore-rules`. Their prompt is a bounded task packet rather
than primary memory or full conversation history. File, terminal, and test
workers fail closed by default because the installed profiles currently use the
host-local terminal backend. Set `allow_terminal_workers: true` only after the
profiles have a verified container sandbox. Because Hermes one-shot queries
currently use a CLI argument, the task packet can be briefly visible to
same-host process inspection; `local_only` requests are executed through the
allowlisted local endpoint for every LLM call in the turn. Only local file and
todo tools remain visible on those turns; terminal, code execution, web,
browser, messaging, and other network-capable tools are removed.

`local_large` currently fails closed with an availability message. Enabling it
requires the exclusive Qwen 27B coordinator from spec 008.
