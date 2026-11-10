"""Parsers for Cisco IOS "show" output (IPv4 and IPv6).

They work on plain text, so the same code reads
  * live output collected over SSH by Netmiko, and
  * snapshots copied from the Packet Tracer CLI (offline mode).

Every parser returns plain dicts/lists, never raises on unknown lines,
and reports how complete the output looked (see `ParsedRoutes.complete`).
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_IF_SHORT = {
    "gi": "GigabitEthernet", "gig": "GigabitEthernet",
    "fa": "FastEthernet", "te": "TenGigabitEthernet",
    "lo": "Loopback", "se": "Serial", "vl": "Vlan", "nu": "Null",
}


def normalize_ifname(name: str) -> str:
    """'Gi0/1' -> 'GigabitEthernet0/1', 'gigabitethernet0/1' -> same."""
    m = re.match(r"([A-Za-z-]+)\s*([\d/.:]+)$", name.strip())
    if not m:
        return name.strip()
    word, num = m.groups()
    low = word.lower()
    for full in set(_IF_SHORT.values()):
        if full.lower() == low:
            return full + num
    for short, full in sorted(_IF_SHORT.items(), key=lambda kv: -len(kv[0])):
        if low.startswith(short) and full.lower().startswith(low):
            return full + num
    return word + num


# ---------------------------------------------------------------------------
# Splitting a captured session into per-command outputs
# ---------------------------------------------------------------------------
COMMANDS = {
    "ip_int_brief": "show ip interface brief",
    "ipv6_int_brief": "show ipv6 interface brief",
    "ip_route": "show ip route",
    "ipv6_route": "show ipv6 route",
    "ospf_neighbors": "show ip ospf neighbor",
    "ospfv3_neighbors": "show ipv6 ospf neighbor",
}

_PROMPT = re.compile(r"^(?P<host>[A-Za-z][\w.-]*)(?:\([\w-]+\))?[>#]\s*(?P<cmd>.*)$")


def match_command(typed: str) -> str | None:
    """Map what the user typed (possibly abbreviated) to a COMMANDS key.

    'sh ip int br' -> 'ip_int_brief'. A typo such as 'how ipv6 route'
    matches nothing and returns None.
    """
    words = typed.lower().split()
    for key, full in COMMANDS.items():
        canon = full.split()
        if len(words) == len(canon) and all(c.startswith(w) for w, c in zip(words, canon)):
            return key
    return None


@dataclass
class CommandOutput:
    command: str          # what was typed
    text: str             # output lines (without the prompt line)
    ok: bool              # False if IOS answered "% Invalid input" etc.


@dataclass
class Session:
    hostname: str | None
    outputs: dict[str, CommandOutput] = field(default_factory=dict)
    unrecognized: list[str] = field(default_factory=list)  # typed but unknown


def split_session(text: str) -> Session:
    """Cut a CLI transcript into blocks, one per command."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    session = Session(hostname=None)
    current: tuple[str, list[str]] | None = None

    def flush():
        if current is None:
            return
        typed, body = current
        out = "\n".join(body).strip("\n")
        key = match_command(typed)
        failed = "% Invalid input" in out or "% Incomplete command" in out
        if key is None:
            if typed.strip() and typed.strip().lower() not in ("enable", "en"):
                session.unrecognized.append(typed.strip())
            return
        # keep the last successful run of a command
        if key not in session.outputs or not failed:
            session.outputs[key] = CommandOutput(typed.strip(), out, not failed)

    for line in lines:
        m = _PROMPT.match(line.strip("\t "))
        if m and not line.startswith(" "):
            flush()
            session.hostname = session.hostname or m.group("host")
            current = (m.group("cmd"), [])
        elif current is not None:
            current[1].append(line)
    flush()
    return session


# ---------------------------------------------------------------------------
# show ip interface brief
# ---------------------------------------------------------------------------
_IP_BRIEF = re.compile(
    r"^(?P<intf>\S+)\s+(?P<ip>\S+)\s+(?:YES|NO)\s+\S+\s+"
    r"(?P<status>administratively down|up|down)\s+(?P<proto>up|down)\s*$"
)


def parse_ip_int_brief(text: str) -> dict[str, dict]:
    result = {}
    for line in text.splitlines():
        m = _IP_BRIEF.match(line.strip())
        if m:
            ip = m.group("ip")
            result[normalize_ifname(m.group("intf"))] = {
                "ipv4": None if ip == "unassigned" else ip,
                "status": m.group("status"),
                "protocol": m.group("proto"),
            }
    return result


# ---------------------------------------------------------------------------
# show ipv6 interface brief
# ---------------------------------------------------------------------------
_V6_BRIEF_IF = re.compile(r"^(?P<intf>\S+)\s+\[(?P<status>[^/\]]+)/(?P<proto>[^\]]+)\]")


def parse_ipv6_int_brief(text: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    current = None
    for line in text.splitlines():
        m = _V6_BRIEF_IF.match(line)
        if m:
            current = normalize_ifname(m.group("intf"))
            result[current] = {"status": m.group("status").strip(),
                               "protocol": m.group("proto").strip(),
                               "link_local": None, "global": []}
            continue
        addr = line.strip()
        if current is None or not addr or addr == "unassigned":
            continue
        try:
            ip = ipaddress.IPv6Address(addr.split()[0])
        except ValueError:
            continue
        if ip.is_link_local:
            result[current]["link_local"] = str(ip)
        else:
            result[current]["global"].append(str(ip))
    return result


# ---------------------------------------------------------------------------
# Routing tables
# ---------------------------------------------------------------------------
@dataclass
class Route:
    prefix: str                       # normalised, e.g. "10.12.20.0/24"
    protocol: str                     # C, L, S, O, O IA, ...
    next_hops: list[str] = field(default_factory=list)
    interfaces: list[str] = field(default_factory=list)


@dataclass
class ParsedRoutes:
    routes: dict[str, Route]
    expected_count: int | None        # what the header says
    complete: bool                    # header count == parsed count

    def by_protocol(self, code: str) -> list[Route]:
        return [r for r in self.routes.values() if r.protocol.split()[0] == code]


_V4_HEADER = re.compile(
    r"^\s+(?P<net>\d+\.\d+\.\d+\.\d+)(?:/(?P<len>\d+))? is "
    r"(?:variably )?subnetted, (?P<n>\d+) subnets"
)
_V4_ROUTE = re.compile(
    r"^(?P<code>[A-Za-z][A-Za-z0-9]*(?: [A-Z0-9]{1,2})?\*?)\s+"
    r"(?P<net>\d+\.\d+\.\d+\.\d+)(?:/(?P<len>\d+))?\s+(?P<rest>.*)$"
)
_V4_VIA = re.compile(r"via (?P<nh>\d+\.\d+\.\d+\.\d+)(?:,.*?)?(?:, (?P<intf>[A-Za-z][\w/.]+))?\s*$")
_V4_CONT = re.compile(r"^\s+\[\d+/\d+\] via (?P<nh>\d+\.\d+\.\d+\.\d+)(?:,.*?)?(?:, (?P<intf>[A-Za-z][\w/.]+))?\s*$")


def parse_ip_route(text: str) -> ParsedRoutes:
    routes: dict[str, Route] = {}
    expected = 0
    saw_header = False
    header_len: int | None = None        # for "x.x.x.x/24 is subnetted" (one mask)
    last: Route | None = None
    for line in text.splitlines():
        h = _V4_HEADER.match(line)
        if h:
            saw_header = True
            expected += int(h.group("n"))
            header_len = int(h.group("len")) if (h.group("len") and
                                                 "variably" not in line) else None
            continue
        c = _V4_CONT.match(line)
        if c and last is not None:
            last.next_hops.append(c.group("nh"))
            if c.group("intf"):
                last.interfaces.append(normalize_ifname(c.group("intf")))
            continue
        m = _V4_ROUTE.match(line)
        if not m or m.group("code").startswith(("Codes", "Gateway")):
            continue
        plen = m.group("len") or header_len
        if plen is None:
            continue
        net = ipaddress.ip_network(f"{m.group('net')}/{plen}", strict=False)
        rest = m.group("rest")
        route = Route(prefix=str(net), protocol=m.group("code").rstrip("*").strip())
        v = _V4_VIA.search(rest)
        if v:
            route.next_hops.append(v.group("nh"))
            if v.group("intf"):
                route.interfaces.append(normalize_ifname(v.group("intf")))
        dc = re.search(r"directly connected, (\S+)", rest)
        if dc:
            route.interfaces.append(normalize_ifname(dc.group(1)))
        routes[route.prefix] = route
        last = route
    exp = expected if saw_header else None
    return ParsedRoutes(routes, exp, exp is None or exp == len(routes))


_V6_TOTAL = re.compile(r"IPv6 Routing Table - (?:\S+ - )?(?P<n>\d+) entries")
_V6_ROUTE = re.compile(r"^(?P<code>[A-Z][A-Z0-9]*)\s+(?P<prefix>[0-9A-Fa-f:]+/\d+)\s+\[\d+/\d+\]")
_V6_VIA = re.compile(r"^\s+via (?P<a>[^,\s]+)(?:, (?P<b>\S+))?")


def parse_ipv6_route(text: str) -> ParsedRoutes:
    routes: dict[str, Route] = {}
    expected = None
    last: Route | None = None
    for line in text.splitlines():
        t = _V6_TOTAL.search(line)
        if t:
            expected = int(t.group("n"))
            continue
        m = _V6_ROUTE.match(line)
        if m:
            net = ipaddress.IPv6Network(m.group("prefix"), strict=False)
            last = Route(prefix=str(net), protocol=m.group("code"))
            routes[last.prefix] = last
            continue
        v = _V6_VIA.match(line)
        if v and last is not None:
            a, b = v.group("a"), v.group("b") or ""
            try:
                ipaddress.IPv6Address(a)
                last.next_hops.append(a.lower())
                if b:
                    last.interfaces.append(normalize_ifname(b))
            except ValueError:            # "via GigabitEthernet0/0, directly connected"
                last.interfaces.append(normalize_ifname(a))
    return ParsedRoutes(routes, expected, expected is None or expected == len(routes))


# ---------------------------------------------------------------------------
# OSPFv2 / OSPFv3 neighbors
# ---------------------------------------------------------------------------
_NBR = re.compile(
    r"^(?P<rid>\d+\.\d+\.\d+\.\d+)\s+(?P<pri>\d+)\s+(?P<state>\w+/\s*[\w-]*)\s+"
    r"(?P<dead>\S+)\s+(?P<addr>\S+)\s+(?P<intf>\S+)\s*$"
)


def parse_ospf_neighbors(text: str) -> list[dict]:
    """Works for both 'show ip ospf neighbor' and 'show ipv6 ospf neighbor'."""
    result = []
    for line in text.splitlines():
        m = _NBR.match(line.strip())
        if m:
            state = re.sub(r"\s+", "", m.group("state"))
            result.append({
                "router_id": m.group("rid"),
                "state": state.split("/")[0],
                "role": state.split("/")[1] if "/" in state else "",
                "interface": normalize_ifname(m.group("intf")),
            })
    return result


# ---------------------------------------------------------------------------
# One call: transcript -> facts
# ---------------------------------------------------------------------------
PARSERS = {
    "ip_int_brief": parse_ip_int_brief,
    "ipv6_int_brief": parse_ipv6_int_brief,
    "ip_route": parse_ip_route,
    "ipv6_route": parse_ipv6_route,
    "ospf_neighbors": parse_ospf_neighbors,
    "ospfv3_neighbors": parse_ospf_neighbors,
}


def facts_from_outputs(outputs: dict[str, str], hostname: str | None = None) -> dict:
    """Parse {command_key: raw_text}; keys without data map to None."""
    facts: dict = {"hostname": hostname}
    for key, parser in PARSERS.items():
        raw = outputs.get(key)
        facts[key] = parser(raw) if raw is not None else None
    return facts


def facts_from_transcript(text: str) -> tuple[dict, Session]:
    session = split_session(text)
    outputs = {k: o.text for k, o in session.outputs.items() if o.ok}
    return facts_from_outputs(outputs, session.hostname), session
