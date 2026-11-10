"""Load the intended-state YAML and check it before anything touches a device.

The checks here are the "pre-deployment" half of compliance validation:
a wrong plan is caught on the laptop, not on the routers.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class Interface:
    name: str
    description: str
    ipv4: ipaddress.IPv4Interface | None
    ipv6: ipaddress.IPv6Interface | None
    link_local: ipaddress.IPv6Address | None = None
    lan: bool = False
    peer: str | None = None  # "R2:GigabitEthernet0/1"

    # Values the templates need, precomputed so templates stay simple
    @property
    def ipv4_addr(self) -> str:
        return str(self.ipv4.ip)

    @property
    def ipv4_mask(self) -> str:
        return str(self.ipv4.netmask)

    @property
    def ipv4_network(self) -> str:
        return str(self.ipv4.network.network_address)

    @property
    def ipv4_wildcard(self) -> str:
        return str(self.ipv4.network.hostmask)


@dataclass
class StaticRoute:
    prefix: ipaddress.IPv4Network | ipaddress.IPv6Network
    next_hop: str

    @property
    def version(self) -> int:
        return self.prefix.version


@dataclass
class Router:
    name: str
    role: str
    loopback4: ipaddress.IPv4Interface
    loopback6: ipaddress.IPv6Interface
    interfaces: list[Interface]
    static_routes: list[StaticRoute] = field(default_factory=list)

    @property
    def router_id(self) -> str:
        return str(self.loopback4.ip)

    @property
    def lan_interfaces(self) -> list[Interface]:
        return [i for i in self.interfaces if i.lan]


@dataclass
class Host:
    name: str
    connected_to: str
    ipv4: ipaddress.IPv4Interface
    gateway4: ipaddress.IPv4Address
    ipv6: ipaddress.IPv6Interface
    gateway6: ipaddress.IPv6Address


@dataclass
class Plan:
    meta: dict
    defaults: dict
    routers: dict[str, Router]
    hosts: dict[str, Host]

    @property
    def ospf_process(self) -> int:
        return self.defaults["ospf"]["process_id"]

    @property
    def ospf_area(self) -> int:
        return self.defaults["ospf"]["area"]


class PlanError(Exception):
    """Raised when the YAML cannot even be parsed into the model."""


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_plan(path: str | Path) -> Plan:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    try:
        routers = {
            name: Router(
                name=name,
                role=r.get("role", ""),
                loopback4=ipaddress.IPv4Interface(r["loopback0"]["ipv4"]),
                loopback6=ipaddress.IPv6Interface(r["loopback0"]["ipv6"]),
                interfaces=[
                    Interface(
                        name=ifname,
                        description=i.get("description", ""),
                        ipv4=(ipaddress.IPv4Interface(i["ipv4"])
                              if i.get("ipv4") else None),
                        ipv6=(ipaddress.IPv6Interface(i["ipv6"])
                              if i.get("ipv6") else None),
                        link_local=(
                            ipaddress.IPv6Address(i["link_local"])
                            if i.get("link_local") else None
                        ),
                        lan=bool(i.get("lan", False)),
                        peer=i.get("peer"),
                    )
                    for ifname, i in r["interfaces"].items()
                ],
                static_routes=[
                    StaticRoute(
                        prefix=ipaddress.ip_network(s["prefix"]),
                        next_hop=str(s["next_hop"]),
                    )
                    for s in (r.get("static_routes") or [])
                ],
            )
            for name, r in raw["routers"].items()
        }
        hosts = {
            name: Host(
                name=name,
                connected_to=h["connected_to"],
                ipv4=ipaddress.IPv4Interface(h["ipv4"]),
                gateway4=ipaddress.IPv4Address(h["gateway4"]),
                ipv6=ipaddress.IPv6Interface(h["ipv6"]),
                gateway6=ipaddress.IPv6Address(h["gateway6"]),
            )
            for name, h in (raw.get("hosts") or {}).items()
        }
    except (KeyError, ValueError) as exc:  # missing key or bad address
        raise PlanError(f"Cannot parse plan: {exc!r}") from exc

    return Plan(meta=raw.get("meta", {}), defaults=raw["defaults"],
                routers=routers, hosts=hosts)


# ---------------------------------------------------------------------------
# Pre-deployment checks ("plan lint")
# ---------------------------------------------------------------------------
def lint_plan(plan: Plan) -> list[str]:
    """Return a list of problems; an empty list means the plan is consistent."""
    errors: list[str] = []
    v4_block = ipaddress.ip_network(plan.meta.get("ipv4_block", "0.0.0.0/0"))
    v6_block = ipaddress.ip_network(plan.meta.get("ipv6_block", "::/0"))

    seen_v4: dict[ipaddress.IPv4Address, str] = {}
    seen_v6: dict[ipaddress.IPv6Address, str] = {}
    by_name = {
        f"{r.name}:{i.name}": i for r in plan.routers.values() for i in r.interfaces
    }

    def claim(addr, owner, seen):
        if addr in seen:
            errors.append(f"Duplicate address {addr}: {seen[addr]} and {owner}")
        seen[addr] = owner

    for r in plan.routers.values():
        # Loopbacks
        for lo, block in ((r.loopback4, v4_block), (r.loopback6, v6_block)):
            if lo.ip not in block:
                errors.append(f"{r.name} Loopback0 {lo} is outside {block}")
        claim(r.loopback4.ip, f"{r.name}:Loopback0", seen_v4)
        claim(r.loopback6.ip, f"{r.name}:Loopback0", seen_v6)

        for i in r.interfaces:
            owner = f"{r.name}:{i.name}"
            # Dual-stack rule: every routed interface carries both protocols
            if i.ipv4 is None or i.ipv6 is None:
                missing = "IPv4" if i.ipv4 is None else "IPv6"
                errors.append(f"{owner} is not dual-stack (no {missing} address)")
                continue
            if i.ipv4.ip not in v4_block:
                errors.append(f"{owner} IPv4 {i.ipv4} is outside {v4_block}")
            if i.ipv6.ip not in v6_block:
                errors.append(f"{owner} IPv6 {i.ipv6} is outside {v6_block}")
            # Host part must not be the network or broadcast address
            net4 = i.ipv4.network
            if net4.prefixlen < 31 and i.ipv4.ip in (net4.network_address,
                                                     net4.broadcast_address):
                errors.append(f"{owner} IPv4 {i.ipv4.ip} is a network/broadcast address")
            if i.ipv6.ip == i.ipv6.network.network_address:
                errors.append(f"{owner} IPv6 {i.ipv6.ip} is the subnet-router anycast address")
            if i.link_local and not i.link_local.is_link_local:
                errors.append(f"{owner} link-local {i.link_local} is not in fe80::/10")
            claim(i.ipv4.ip, owner, seen_v4)
            claim(i.ipv6.ip, owner, seen_v6)

            # Point-to-point links: both ends must agree with each other
            if i.peer:
                other = by_name.get(i.peer)
                if other is None:
                    errors.append(f"{owner} peer {i.peer} does not exist in the plan")
                    continue
                if other.ipv4 is None or other.ipv6 is None:
                    continue  # already reported as "not dual-stack"
                if other.peer != owner:
                    errors.append(f"{owner} -> {i.peer}, but the peer points to {other.peer}")
                if other.ipv4.network != i.ipv4.network:
                    errors.append(f"{owner} and {i.peer} are in different IPv4 subnets")
                if other.ipv6.network != i.ipv6.network:
                    errors.append(f"{owner} and {i.peer} are in different IPv6 subnets")

    # Different links must not overlap (same link = same subnet is fine)
    links4: dict[ipaddress.IPv4Network, set[str]] = {}
    links6: dict[ipaddress.IPv6Network, set[str]] = {}
    for r in plan.routers.values():
        for i in r.interfaces:
            if i.ipv4 is None or i.ipv6 is None:
                continue
            links4.setdefault(i.ipv4.network, set()).add(f"{r.name}:{i.name}")
            links6.setdefault(i.ipv6.network, set()).add(f"{r.name}:{i.name}")
    for links in (links4, links6):
        nets = sorted(links, key=lambda n: (n.network_address, n.prefixlen))
        for a_idx, a in enumerate(nets):
            for b in nets[a_idx + 1:]:
                if a.overlaps(b):
                    errors.append(f"Subnets {a} and {b} overlap")

    # Hosts must sit in the LAN of the router port they are cabled to
    for h in plan.hosts.values():
        port = by_name.get(h.connected_to)
        if port is None:
            errors.append(f"{h.name} is connected to unknown port {h.connected_to}")
            continue
        if port.ipv4 is None or port.ipv6 is None:
            continue
        if h.ipv4.network != port.ipv4.network:
            errors.append(f"{h.name} IPv4 {h.ipv4} is not in {port.ipv4.network}")
        if h.ipv6.network != port.ipv6.network:
            errors.append(f"{h.name} IPv6 {h.ipv6} is not in {port.ipv6.network}")
        if h.gateway4 != port.ipv4.ip:
            errors.append(f"{h.name} gateway {h.gateway4} != router address {port.ipv4.ip}")
        if port.link_local and h.gateway6 != port.link_local:
            errors.append(f"{h.name} IPv6 gateway {h.gateway6} != router link-local {port.link_local}")
        claim(h.ipv4.ip, h.name, seen_v4)
        claim(h.ipv6.ip, h.name, seen_v6)

    return errors
