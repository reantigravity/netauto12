#!/usr/bin/env python3
"""Compliance validation for the dual-stack network (IPv4 + IPv6).

Compares the intended plan with the actual device state and prints a
PASS / FAIL / WARN / SKIP report.

Usage:
    # offline: CLI transcripts copied from Packet Tracer (snapshots/pt/R1.txt ...)
    python validate.py --offline snapshots/pt --platform pt
    python validate.py --offline snapshots/pt --platform pt --all   # show every check
    python validate.py --offline snapshots/pt --csv reports/compliance.csv

Exit code: 0 = no FAIL, 1 = at least one FAIL, 2 = bad input.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

from netauto.compliance import FAIL, PASS, SKIP, WARN, Checker, summarize
from netauto.parsers import facts_from_transcript
from netauto.plan import PlanError, load_plan
from netauto.platforms import CAPABILITIES

ROOT = Path(__file__).resolve().parent
USE_COLOR = sys.stdout.isatty()
COLORS = {PASS: "32", FAIL: "31", WARN: "33", SKIP: "90"}


def paint(text: str, status: str) -> str:
    return f"\033[{COLORS[status]}m{text}\033[0m" if USE_COLOR else text


def load_offline(directory: Path, devices) -> tuple[dict, list[str]]:
    facts, notes = {}, []
    for name in devices:
        path = directory / f"{name}.txt"
        if not path.exists():
            facts[name] = None
            notes.append(f"{name}: no snapshot file {path}")
            continue
        f, session = facts_from_transcript(path.read_text(encoding="utf-8", errors="replace"))
        facts[name] = f
        for typed in session.unrecognized:
            notes.append(f"{name}: ignored unknown/failed command '{typed}'")
    return facts, notes


def print_report(results, show_all: bool) -> None:
    rows = [r for r in results if show_all or r.status != PASS]
    if rows:
        w_dev = max(len("Device"), *(len(r.device) for r in rows))
        w_chk = min(max(len("Check"), *(len(r.check) for r in rows)), 56)
        print(f"{'Device':<{w_dev}}  {'Category':<10}  {'Check':<{w_chk}}  {'Proto':<5}  "
              f"{'Status':<6}  Expected -> Actual")
        print("-" * (w_dev + w_chk + 60))
        for r in rows:
            extra = f"{r.expected} -> {r.actual}" if (r.expected or r.actual) else ""
            if r.detail:
                extra += f"  ({r.detail})"
            print(f"{r.device:<{w_dev}}  {r.category:<10}  {r.check[:w_chk]:<{w_chk}}  "
                  f"{r.family:<5}  {paint(f'{r.status:<6}', r.status)}  {extra}")
    else:
        print("All checks passed.")


def print_matrix(results, devices) -> None:
    """Per device x protocol summary - the slide-friendly view."""
    print(f"\n{'Device':<8}{'IPv4 pass/total':>18}{'IPv6 pass/total':>18}{'FAIL':>6}{'WARN':>6}")
    for d in devices:
        line = f"{d:<8}"
        for fam in ("IPv4", "IPv6"):
            sub = [r for r in results if r.device == d and r.family == fam
                   and r.status in (PASS, FAIL)]
            ok = sum(1 for r in sub if r.status == PASS)
            line += f"{f'{ok}/{len(sub)}':>18}"
        n_fail = sum(1 for r in results if r.device == d and r.status == FAIL)
        n_warn = sum(1 for r in results if r.device == d and r.status == WARN)
        line += f"{n_fail:>6}{n_warn:>6}"
        print(line)


def write_csv(results, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["device", "category", "check", "family", "status", "expected", "actual", "detail"])
        for r in results:
            w.writerow([r.device, r.category, r.check, r.family, r.status,
                        r.expected, r.actual, r.detail])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", default=ROOT / "intended" / "network.yaml", type=Path)
    ap.add_argument("--offline", type=Path, required=True,
                    help="folder with <device>.txt CLI transcripts")
    ap.add_argument("--platform", choices=sorted(CAPABILITIES), default="pt")
    ap.add_argument("--all", action="store_true", help="also list passed checks")
    ap.add_argument("--csv", type=Path, help="write every result to this CSV file")
    args = ap.parse_args()

    try:
        plan = load_plan(args.plan)
    except PlanError as exc:
        print(f"[FAIL] {exc}")
        return 2
    if not args.offline.is_dir():
        print(f"[FAIL] {args.offline} is not a folder")
        return 2

    devices = list(plan.routers)
    facts, notes = load_offline(args.offline, devices)
    results = Checker(plan, CAPABILITIES[args.platform]).run(facts)

    print(f"Compliance report  {datetime.now():%Y-%m-%d %H:%M}  "
          f"source={args.offline}  platform={args.platform}\n")
    for n in notes:
        print(f"  note: {n}")
    if notes:
        print()
    print_report(results, args.all)
    print_matrix(results, devices)
    s = summarize(results)
    print(f"\nChecks: {s[PASS]} PASS, {s[FAIL]} FAIL, {s[WARN]} WARN, {s[SKIP]} SKIP  "
          f"->  compliance {s['score']}% (PASS / (PASS+FAIL))")
    if args.csv:
        write_csv(results, args.csv)
        print(f"CSV written to {args.csv}")
    return 1 if s[FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
