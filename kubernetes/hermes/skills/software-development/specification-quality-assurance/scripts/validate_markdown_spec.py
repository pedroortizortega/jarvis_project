#!/usr/bin/env python3
"""Structural quality checks for Markdown specification artifacts."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("file", type=Path, help="Markdown specification to validate")
    p.add_argument(
        "--requirement-pattern",
        default=r"REQ-[A-Z0-9_-]+-\d{3}",
        help="Regex for requirement identifiers",
    )
    p.add_argument(
        "--acceptance-pattern",
        default=r"AC-\d{3}",
        help="Regex for acceptance criterion identifiers",
    )
    return p


def duplicates(values: list[str]) -> list[str]:
    return sorted({value for value in values if values.count(value) > 1})


def main() -> int:
    args = parser().parse_args()
    try:
        text = args.file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        print(f"FAIL: cannot read {args.file}: {exc}", file=sys.stderr)
        return 2

    errors: list[str] = []
    if not text.strip():
        errors.append("file is empty")

    req_re = re.compile(rf"^###\s+({args.requirement_pattern})\b.*$", re.MULTILINE)
    requirements = req_re.findall(text)
    if not requirements:
        errors.append("no requirement headings found")

    repeated_requirements = duplicates(requirements)
    if repeated_requirements:
        errors.append("duplicate requirement IDs: " + ", ".join(repeated_requirements))

    scenario_heading = re.compile(r"^####\s+(?:Scenario|Escenario):", re.MULTILINE | re.IGNORECASE)
    scenario_count = len(scenario_heading.findall(text))

    matches = list(req_re.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        if not scenario_heading.search(text, match.end(), end):
            errors.append(f"{match.group(1)} has no Scenario/Escenario heading")

    acceptance_re = re.compile(
        rf"^-\s*\[\s*\]\s+({args.acceptance_pattern})\b", re.MULTILINE
    )
    acceptance = acceptance_re.findall(text)
    repeated_acceptance = duplicates(acceptance)
    if repeated_acceptance:
        errors.append("duplicate acceptance IDs: " + ", ".join(repeated_acceptance))

    trace_match = re.search(
        r"^##\s+.*(?:Traceability|Trazabilidad).*$",
        text,
        re.MULTILINE | re.IGNORECASE,
    )
    if trace_match:
        next_heading = re.search(r"^##\s+", text[trace_match.end() :], re.MULTILINE)
        trace_end = trace_match.end() + next_heading.start() if next_heading else len(text)
        trace = text[trace_match.end() : trace_end]
        missing = [rid for rid in requirements if rid not in trace]
        if missing:
            errors.append("requirements missing from traceability: " + ", ".join(missing))

    if errors:
        print(f"FAIL: {args.file}")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"PASS: {args.file}")
    print(f"requirements={len(requirements)}")
    print(f"scenarios={scenario_count}")
    print(f"acceptance_criteria={len(acceptance)}")
    print(f"lines={len(text.splitlines())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
