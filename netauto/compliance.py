"""Compliance validation: compare the intended plan with the actual state.

Input : Plan (intended/network.yaml) + facts per device (from parsers.py)
Output: a flat list of CheckResult rows, one per (device, check, protocol).

Status meaning
  PASS  actual state matches the plan
  FAIL  actual state contradicts the plan          -> must be fixed
  WARN  cannot confirm, or not supported on platform -> needs attention
  SKIP  no data collected for this check
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from .plan import Plan, Router

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"


@dataclass
class CheckResult:
    device: str
    category: str      # Data | Addressing | State | Dual-stack | OSPF | Routing
    check: str
    family: str        # IPv4 | IPv6 | -
    status: str
    expected: str = ""
    actual: str = ""
    detail: str = ""


class Checker:
    def __init__(self, plan: Plan, caps: dict):
        self.plan = plan
        self.caps = caps
        self.results: list[CheckResult] = []

    def add(self, *args, **kwargs):
        self.results.append(CheckResult(*args, **kwargs))

    # -----------------------------------------------------------------
    def run(self, facts_by_device: dict[str, dict | None]) -> list[CheckResult]:
        for name, router in self.plan.routers.items():
            facts = facts_by_device.get(name)
            if facts is None:
                self.add(name, "Data", "device facts collected", "-", FAIL,
                         "snapshot / SSH output", "nothing", "device not reachable or no snapshot")
                continue
            self.check_data(router, facts)
            self.check_interfaces(router, facts)
            self.check_dual_stack(router, facts)
            self.check_ospf(router, facts)
            self.check_routes(router, facts, 4)
            self.check_routes(router, facts, 6)
            self.check_static(router, facts)
        return self.results

    # -----------------------------------------------------------------
    # 0. Quality of the collected data
    # -----------------------------------------------------------------
    def check_data(self, r: Router, f: dict):
        if f.get("hostname") and f["hostname"] != r.name:
            self.add(r.name, "Data", "hostname", "-", FAIL, r.name, f["hostname"])
        else:
            self.add(r.name, "Data", "hostname", "-", PASS, r.name, f.get("hostname") or r.name)
        for key, label, fam in (("ip_int_brief", "show ip interface brief", "IPv4"),
                                ("ipv6_int_brief", "show ipv6 interface brief", "IPv6"),
                                ("ip_route", "show ip route", "IPv4"),
                                ("ipv6_route", "show ipv6 route", "IPv6"),
                                ("ospf_neighbors", "show ip ospf neighbor", "IPv4"),
                                ("ospfv3_neighbors", "show ipv6 ospf neighbor", "IPv6")):
            if f.get(key) is None:
                self.add(r.name, "Data", f"output of '{label}'", fam, WARN,
                         "present", "missing", "command not captured or failed; dependent checks skipped")
        for key, fam in (("ip_route", "IPv4"), ("ipv6_route", "IPv6")):
            rt = f.get(key)
            if rt is not None and not rt.complete:
                self.add(r.name, "Data", "routing table complete", fam, WARN,
                         f"{rt.expected_count} routes (header)", f"{len(rt.routes)} parsed",
                         "output looks truncated; missing routes reported as WARN, not FAIL")

    # -----------------------------------------------------------------
    # 1. Addresses and state of every planned interface
    # -----------------------------------------------------------------
    def _planned_interfaces(self, r: Router):
        """Yield (name, ipv4_iface, ipv6_iface, link_local) incl. Loopback0."""
        yield "Loopback0", r.loopback4, r.loopback6, None
        for i in r.interfaces:
            yield i.name, i.ipv4, i.ipv6, i.link_local

    def check_interfaces(self, r: Router, f: dict):
        v4 = f.get("ip_int_brief")
        v6 = f.get("ipv6_int_brief")
        rt4 = f.get("ip_route")
        rt6 = f.get("ipv6_route")
        for name, a4, a6, ll in self._planned_interfaces(r):
            # ---- IPv4 ----
            if v4 is None:
                self.add(r.name, "Addressing", f"{name} address", "IPv4", SKIP)
            elif name not in v4:
                self.add(r.name, "Addressing", f"{name} address", "IPv4", FAIL,
                         str(a4.ip), "interface missing")
            else:
                got = v4[name]["ipv4"]
                ok = got == str(a4.ip)
                self.add(r.name, "Addressing", f"{name} address", "IPv4",
                         PASS if ok else FAIL, str(a4.ip), got or "unassigned")
                state = f"{v4[name]['status']}/{v4[name]['protocol']}"
                self.add(r.name, "State", f"{name} up/up", "IPv4",
                         PASS if state == "up/up" else FAIL, "up/up", state)
                # Mask is not shown by "brief": confirm via the connected route
                if rt4 is not None and ok and a4.network.prefixlen < 32:
                    conn = rt4.routes.get(str(a4.network))
                    if conn and conn.protocol == "C":
                        self.add(r.name, "Addressing", f"{name} prefix length", "IPv4",
                                 PASS, f"/{a4.network.prefixlen}", f"C {conn.prefix}")
                    else:
                        self.add(r.name, "Addressing", f"{name} prefix length", "IPv4",
                                 WARN if not rt4.complete else FAIL,
                                 f"C {a4.network}", "no such connected route")
            # ---- IPv6 ----
            if v6 is None:
                self.add(r.name, "Addressing", f"{name} address", "IPv6", SKIP)
                continue
            if name not in v6:
                self.add(r.name, "Addressing", f"{name} address", "IPv6", FAIL,
                         str(a6.ip), "interface missing")
                continue
            info = v6[name]
            want = str(a6.ip)
            self.add(r.name, "Addressing", f"{name} address", "IPv6",
                     PASS if want in info["global"] else FAIL,
                     want, ", ".join(info["global"]) or "none")
            extra = [g for g in info["global"] if g != want]
            if extra:
                self.add(r.name, "Addressing", f"{name} unplanned addresses", "IPv6", WARN,
                         "none", ", ".join(extra))
            if ll is not None:
                self.add(r.name, "Addressing", f"{name} link-local", "IPv6",
                         PASS if info["link_local"] == str(ll) else FAIL,
                         str(ll), info["link_local"] or "none")
            state = f"{info['status']}/{info['protocol']}"
            self.add(r.name, "State", f"{name} up/up", "IPv6",
                     PASS if state == "up/up" else FAIL, "up/up", state)
            if rt6 is not None and a6.network.prefixlen < 128:
                conn = rt6.routes.get(str(a6.network))
                status = PASS if conn and conn.protocol == "C" else (
                    WARN if not rt6.complete else FAIL)
                self.add(r.name, "Addressing", f"{name} prefix length", "IPv6", status,
                         f"C {a6.network}", f"C {conn.prefix}" if conn else "no such connected route")

    # -----------------------------------------------------------------
    # 2. Dual-stack parity: anything routed over IPv4 must also do IPv6
    # -----------------------------------------------------------------
    def check_dual_stack(self, r: Router, f: dict):
        v4, v6 = f.get("ip_int_brief"), f.get("ipv6_int_brief")
        if v4 is None or v6 is None:
            self.add(r.name, "Dual-stack", "IPv4/IPv6 parity", "-", SKIP)
            return
        planned = {n for n, *_ in self._planned_interfaces(r)}
        for name, info in v4.items():
            if not info["ipv4"]:
                continue
            has6 = bool(v6.get(name, {}).get("global"))
            self.add(r.name, "Dual-stack", f"{name} has IPv4 and IPv6", "-",
                     PASS if has6 else FAIL, "IPv4 + IPv6", "IPv4 + IPv6" if has6 else "IPv4 only")
            if name not in planned:
                self.add(r.name, "Addressing", f"{name} not in plan", "IPv4", WARN,
                         "not configured", info["ipv4"], "unplanned interface has an address")

    # -----------------------------------------------------------------
    # 3. OSPFv2 / OSPFv3 adjacencies
    # -----------------------------------------------------------------
    def check_ospf(self, r: Router, f: dict):
        expected = {}  # interface -> neighbor router-id
        for i in r.interfaces:
            if i.peer:
                peer_router = self.plan.routers[i.peer.split(":")[0]]
                expected[i.name] = peer_router.router_id
        for key, fam, proto in (("ospf_neighbors", "IPv4", "OSPFv2"),
                                ("ospfv3_neighbors", "IPv6", "OSPFv3")):
            nbrs = f.get(key)
            if nbrs is None:
                self.add(r.name, "OSPF", f"{proto} neighbors", fam, SKIP)
                continue
            for intf, rid in expected.items():
                match = [n for n in nbrs if n["interface"] == intf and n["router_id"] == rid]
                if not match:
                    self.add(r.name, "OSPF", f"{proto} neighbor {rid} on {intf}", fam, FAIL,
                             "FULL", "absent")
                else:
                    st = match[0]["state"]
                    self.add(r.name, "OSPF", f"{proto} neighbor {rid} on {intf}", fam,
                             PASS if st == "FULL" else FAIL, "FULL", f"{st}/{match[0]['role']}")
            known = set(expected.items())
            for n in nbrs:
                if (n["interface"], n["router_id"]) not in known:
                    self.add(r.name, "OSPF", f"{proto} unexpected neighbor {n['router_id']}", fam,
                             WARN, "none", f"on {n['interface']}")

    # -----------------------------------------------------------------
    # 4. Reachability in the routing table: every planned prefix
    # -----------------------------------------------------------------
    def _all_prefixes(self, version: int):
        """prefix -> (owner router, interface) for every network in the plan."""
        out = {}
        for rr in self.plan.routers.values():
            lo = rr.loopback4 if version == 4 else rr.loopback6
            out[str(lo.network)] = (rr.name, "Loopback0")
            for i in rr.interfaces:
                a = i.ipv4 if version == 4 else i.ipv6
                out.setdefault(str(a.network), (rr.name, i.name))
        return out

    def check_routes(self, r: Router, f: dict, version: int):
        fam = f"IPv{version}"
        table = f.get("ip_route" if version == 4 else "ipv6_route")
        if table is None:
            self.add(r.name, "Routing", "routes to all planned prefixes", fam, SKIP,
                     detail="routing table not captured")
            return
        local = {str((r.loopback4 if version == 4 else r.loopback6).network)}
        local |= {str((i.ipv4 if version == 4 else i.ipv6).network) for i in r.interfaces}
        for prefix, (owner, intf) in sorted(self._all_prefixes(version).items(),
                                            key=lambda kv: ipaddress.ip_network(kv[0])):
            want = "C" if prefix in local else "O"
            got = table.routes.get(prefix)
            if got is None:
                status = WARN if not table.complete else FAIL
                self.add(r.name, "Routing", f"route to {prefix}", fam, status, want, "absent",
                         "snapshot truncated" if status == WARN else f"{owner} {intf} unreachable")
                continue
            proto = got.protocol.split()[0]
            # loopbacks are host routes: IOS shows them as C (v4) or LC (v6)
            ok = proto == want or (want == "C" and proto in ("LC", "L"))
            via = ", ".join(got.next_hops or got.interfaces)
            self.add(r.name, "Routing", f"route to {prefix}", fam,
                     PASS if ok else FAIL, want, f"{got.protocol} via {via}")

    # -----------------------------------------------------------------
    # 5. Static routes from the plan (platform aware)
    # -----------------------------------------------------------------
    def check_static(self, r: Router, f: dict):
        for s in r.static_routes:
            fam = f"IPv{s.version}"
            label = f"static {s.prefix} -> {s.next_hop}"
            if s.version == 6 and s.next_hop == "Null0" and not self.caps.get("ipv6_null_route", True):
                self.add(r.name, "Routing", label, fam, WARN, "S", "not deployed",
                         "not supported on this platform (Packet Tracer)")
                continue
            table = f.get("ip_route" if s.version == 4 else "ipv6_route")
            if table is None:
                self.add(r.name, "Routing", label, fam, SKIP)
                continue
            got = table.routes.get(str(s.prefix))
            ok = got is not None and got.protocol.startswith("S") and (
                s.next_hop in got.interfaces or s.next_hop in got.next_hops)
            self.add(r.name, "Routing", label, fam, PASS if ok else FAIL, "S",
                     f"{got.protocol} via {', '.join(got.interfaces + got.next_hops)}" if got else "absent")


def summarize(results: list[CheckResult]) -> dict:
    counts = {s: sum(1 for r in results if r.status == s) for s in (PASS, FAIL, WARN, SKIP)}
    scored = counts[PASS] + counts[FAIL]
    counts["score"] = round(100 * counts[PASS] / scored, 1) if scored else 0.0
    return counts
