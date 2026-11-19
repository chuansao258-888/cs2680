#!/usr/bin/env python3
"""Create a per-task, per-turn API catalogue-price report from agent logs.

This reports API list-price equivalents, not Token Plan Credits.  It uses the
cached-token count recorded in ``latency.jsonl`` and keeps chat and context
compaction calls separate.  A compaction from an older run that lacks usage
fields is shown as ``unknown`` rather than estimated.

Usage:
    python3 evaluation_scripts/cost_report.py
    python3 evaluation_scripts/cost_report.py --logs previous_runs/2026.../madsOpt_logs
    python3 evaluation_scripts/cost_report.py --price qwen3.8-flash,0.8,0.1,2.7
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# RMB per million tokens.  These are API catalogue prices as of 2026-11-19,
# not Token Plan Credits.  Pass --price to override or add a model.
DEFAULT_PRICES = {
    "qwen3.8-flash": (0.8, 0.1, 2.7),  # input, cached input, output
}


@dataclass(frozen=True)
class Price:
    input_per_million: float
    cached_per_million: float
    output_per_million: float


def parse_price(value: str) -> tuple[str, Price]:
    try:
        model, regular, cached, output = value.split(",")
        return model, Price(float(regular), float(cached), float(output))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--price must be MODEL,INPUT,CACHED,OUTPUT (all rates are RMB/M tokens)"
        ) from exc


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid JSON in {path}:{number}: {exc}") from exc
    return rows


def cost(record: dict[str, Any], prices: dict[str, Price]) -> tuple[float | None, str | None]:
    model = record.get("model_id")
    price = prices.get(model)
    if price is None:
        return None, f"no API price configured for {model!r}"

    prompt = record.get("prompt_tokens")
    completion = record.get("completion_tokens")
    cached = record.get("cached_tokens")
    if not isinstance(prompt, int) or not isinstance(completion, int):
        return None, "usage was not logged"
    if cached is None:
        return None, "cached_tokens was not logged"
    if not isinstance(cached, int) or cached < 0 or cached > prompt:
        return None, "invalid cached_tokens"

    uncached = prompt - cached
    value = (
        uncached * price.input_per_million
        + cached * price.cached_per_million
        + completion * price.output_per_million
    ) / 1_000_000
    return value, None


def money(value: float | None) -> str:
    return "unknown" if value is None else f"¥{value:.6f}"


def task_directories(logs: Path) -> list[Path]:
    directories = [path for path in logs.iterdir() if path.is_dir()]
    return sorted(directories, key=lambda path: (not path.name.isdigit(), path.name))


def report_task(task_dir: Path, prices: dict[str, Price]) -> tuple[list[str], float, int]:
    latency = read_jsonl(task_dir / "latency.jsonl")
    lines = [f"## Task {task_dir.name}", "",
             "| turn | kind | model | prompt | cached | uncached | output | cost | note |",
             "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |"]
    total = 0.0
    unknown = 0
    for record in latency:
        kind = record.get("kind", "unknown")
        iteration = record.get("iteration")
        prompt = record.get("prompt_tokens")
        cached = record.get("cached_tokens")
        output = record.get("completion_tokens")
        uncached = prompt - cached if isinstance(prompt, int) and isinstance(cached, int) else None
        value, note = cost(record, prices)
        if value is None:
            unknown += 1
        else:
            total += value
        lines.append(
            f"| {iteration if iteration is not None else '-'} | {kind} | "
            f"{record.get('model_id', '-')} | {prompt if prompt is not None else '-'} | "
            f"{cached if cached is not None else '-'} | {uncached if uncached is not None else '-'} | "
            f"{output if output is not None else '-'} | {money(value)} | {note or ''} |"
        )
    lines.extend(["", f"Task subtotal (known requests): **{money(total)}**."])
    if unknown:
        lines.append(f"**{unknown} request(s) have unknown cost and are excluded from the subtotal.**")
    lines.append("")
    return lines, total, unknown


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs", type=Path, default=Path("madsOpt_logs"),
                        help="directory containing per-task log directories")
    parser.add_argument("--output", type=Path, default=Path("api_cost_report.md"),
                        help="Markdown report path")
    parser.add_argument("--price", action="append", default=[], type=parse_price,
                        help="override/add MODEL,INPUT,CACHED,OUTPUT in RMB per million tokens")
    args = parser.parse_args()

    if not args.logs.is_dir():
        raise SystemExit(f"log directory not found: {args.logs}")
    prices = {model: Price(*rates) for model, rates in DEFAULT_PRICES.items()}
    prices.update(dict(args.price))

    report = [
        "# API catalogue-price cost report",
        "",
        "This is an API list-price equivalent in RMB, not Token Plan Credits.",
        "A chat/compression row is costed as `(prompt - cached) × input + cached × cached-input + output × output`.",
        "",
        "## Price table",
        "",
        "| model | input / M | cached input / M | output / M |",
        "| --- | ---: | ---: | ---: |",
    ]
    for model, price in sorted(prices.items()):
        report.append(
            f"| {model} | ¥{price.input_per_million:g} | "
            f"¥{price.cached_per_million:g} | ¥{price.output_per_million:g} |")

    grand_total = 0.0
    unknown = 0
    directories = task_directories(args.logs)
    if not directories:
        raise SystemExit(f"no task directories in {args.logs}")
    for task_dir in directories:
        lines, subtotal, missing = report_task(task_dir, prices)
        report.extend(["", *lines])
        grand_total += subtotal
        unknown += missing

    report.extend(["## Total", "", f"Known API catalogue-price cost: **{money(grand_total)}**."])
    if unknown:
        report.append(f"**{unknown} request(s) with missing usage are not included.**")
    args.output.write_text("\n".join(report) + "\n", encoding="utf-8")
    # Keep the status line ASCII so the script also works in a Windows GBK console.
    print(f"wrote {args.output} ({grand_total:.6f} RMB known cost)")


if __name__ == "__main__":
    main()
