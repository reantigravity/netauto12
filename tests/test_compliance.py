from pathlib import Path

from netauto.compliance import FAIL, PASS, WARN, Checker
from netauto.parsers import facts_from_transcript
from netauto.plan import load_plan
from netauto.platforms import CAPABILITIES

ROOT = Path(__file__).resolve().parents[1]
PLAN = load_plan(ROOT / "intended" / "network.yaml")


def load(name, folder=ROOT / "snapshots" / "pt"):
    return facts_from_transcript((folder / f"{name}.txt").read_text(encoding="utf-8"))[0]


def run(facts):
    return Checker(PLAN, CAPABILITIES["pt"]).run(facts)


def test_real_packet_tracer_network_has_no_failures():
    results = run({n: load(n) for n in ("R1", "R2", "R3")})
    assert [r for r in results if r.status == FAIL] == []
    assert sum(r.status == PASS for r in results) > 100


def test_ipv6_null_route_is_a_platform_warning_not_a_failure():
    results = run({n: load(n) for n in ("R1", "R2", "R3")})
    row = next(r for r in results if "2001:db8:12::/48" in r.check)
    assert row.status == WARN and "platform" in row.detail


def test_drifted_router_is_caught():
    facts = {n: load(n) for n in ("R1", "R3")}
    facts["R2"] = load("R2_drifted", ROOT / "tests" / "fixtures")
    fails = {(r.device, r.check, r.family) for r in run(facts) if r.status == FAIL}
    assert ("R2", "GigabitEthernet0/0 link-local", "IPv6") in fails
    assert ("R2", "GigabitEthernet0/2 up/up", "IPv4") in fails
    assert ("R2", "GigabitEthernet0/2 up/up", "IPv6") in fails
    assert ("R2", "Vlan1 has IPv4 and IPv6", "-") in fails
    assert ("R2", "OSPFv2 neighbor 10.12.0.3 on GigabitEthernet0/2", "IPv4") in fails
    assert ("R2", "OSPFv3 neighbor 10.12.0.3 on GigabitEthernet0/2", "IPv6") in fails


def test_missing_device_is_a_failure():
    results = run({"R1": load("R1"), "R2": load("R2"), "R3": None})
    assert any(r.device == "R3" and r.status == FAIL for r in results)
