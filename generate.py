#!/usr/bin/env python3
"""Step 1 of the pipeline: plan -> lint -> configs + addressing tables.

Usage:
    python generate.py                 # Packet Tracer configs (default)
    python generate.py --target lab    # adds SSH/management block for Netmiko
    python generate.py --check         # only validate the plan, write nothing
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from netauto.plan import PlanError, lint_plan, load_plan

ROOT = Path(__file__).resolve().parent

# What each target platform can and cannot do. The plan stays the same;
# templates and the validator adapt to the platform.
CAPABILITIES = {
    # Packet Tracer 9 (2911): rejects "ipv6 route <prefix> Null0"
    "pt": {"ipv6_null_route": False},
    # Real IOS / IOS XE (CML, DevNet Sandbox)
    "lab": {"ipv6_null_route": True},
}


def render_configs(plan, target: str, out_dir: Path) -> list[Path]:
    env = Environment(
        loader=FileSystemLoader(ROOT / "templates"),
        undefined=StrictUndefined,      # a typo in a template fails loudly
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    template = env.get_template("router.j2")
    # Credentials never live in the repo: they come from the environment
    mgmt = {
        "username": os.environ.get("NETAUTO_USER", "netauto"),
        "secret": os.environ.get("NETAUTO_SECRET", "CHANGE_ME"),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for r in plan.routers.values():
        text = template.render(
            r=r,
            defaults=plan.defaults,
            ospf={"process": plan.ospf_process, "area": plan.ospf_area},
            target=target,
            caps=CAPABILITIES[target],
            mgmt=mgmt,
        )
        path = out_dir / f"{r.name}.cfg"
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def addressing_rows(plan) -> list[dict]:
    rows = []
    for r in plan.routers.values():
        rows.append({"Device": r.name, "Interface": "Loopback0",
                     "IPv4": str(r.loopback4), "IPv6": str(r.loopback6),
                     "Link-local": "", "Gateway": "N/A"})
        for i in r.interfaces:
            rows.append({"Device": r.name, "Interface": i.name,
                         "IPv4": str(i.ipv4), "IPv6": str(i.ipv6),
                         "Link-local": str(i.link_local or ""), "Gateway": "N/A"})
    for h in plan.hosts.values():
        rows.append({"Device": h.name, "Interface": "NIC",
                     "IPv4": str(h.ipv4), "IPv6": str(h.ipv6), "Link-local": "auto",
                     "Gateway": f"{h.gateway4} / {h.gateway6}"})
    return rows


def write_tables(plan, docs_dir: Path) -> None:
    docs_dir.mkdir(parents=True, exist_ok=True)
    rows = addressing_rows(plan)
    with open(docs_dir / "addressing_table.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    cols = list(rows[0])
    md = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    md += ["| " + " | ".join(row[c] for c in cols) + " |" for row in rows]
    (docs_dir / "addressing_table.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", default=ROOT / "intended" / "network.yaml", type=Path)
    ap.add_argument("--target", choices=["pt", "lab"], default="pt")
    ap.add_argument("--out", default=ROOT / "configs", type=Path)
    ap.add_argument("--check", action="store_true", help="lint the plan only")
    args = ap.parse_args()

    try:
        plan = load_plan(args.plan)
    except PlanError as exc:
        print(f"[FAIL] {exc}")
        return 2

    problems = lint_plan(plan)
    if problems:
        print(f"[FAIL] Plan has {len(problems)} problem(s):")
        for p in problems:
            print(f"   - {p}")
        return 1
    n_if = sum(len(r.interfaces) for r in plan.routers.values())
    print(f"[ OK ] Plan is consistent: {len(plan.routers)} routers, "
          f"{n_if} interfaces, {len(plan.hosts)} hosts, dual-stack everywhere")
    if args.check:
        return 0

    out_dir = args.out / args.target
    for path in render_configs(plan, args.target, out_dir):
        lines = sum(1 for _ in path.open(encoding="utf-8"))
        print(f"[ OK ] {path.relative_to(ROOT)}  ({lines} lines)")
    write_tables(plan, ROOT / "docs")
    print("[ OK ] docs/addressing_table.md and .csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
