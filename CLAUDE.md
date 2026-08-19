# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

This repository is currently empty — it contains only a Python `.gitignore` and an initial commit. There is no source code, build configuration, dependency manifest, or test suite yet.

Treat everything below as a placeholder to fill in as the project takes shape. Do not infer commands or architecture that are not recorded here; verify against the actual files instead.

## Language

Python. Inferred from `.gitignore` (GitHub's Python template — covers `__pycache__/`, packaging artifacts, `.venv/`, pytest/mypy/ruff caches, Django/Flask/Poetry/PDM/uv/pixi entries). No package manager or framework has actually been chosen yet.

## To fill in

Update this file once the corresponding decisions are made and reflected in the repo:

- **Setup / dependency management** — package manager (`uv`, `poetry`, `pip` + `requirements.txt`, …), how to create the environment and install deps.
- **Run** — entry point and command to start the application.
- **Test** — test runner, command for the full suite, and command for a single test file or test case.
- **Lint / format / typecheck** — tool names and invocations.
- **Architecture** — the big-picture structure that spans multiple files: module boundaries, how components communicate, and any non-obvious control or data flow.

## specs
The name of the specs follow the structure like 001_some_name.md and it need to be save in the folder specs
