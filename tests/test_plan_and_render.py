from pathlib import Path

import yaml

from generate import render_configs
from netauto.plan import lint_plan, load_plan

ROOT = Path(__file__).resolve().parents[1]
PLAN_FILE = ROOT / "intended" / "network.yaml"


def test_intended_plan_is_consistent():
    assert lint_plan(load_plan(PLAN_FILE)) == []


def _broken(tmp_path, mutate):
    raw = yaml.safe_load(PLAN_FILE.read_text())
    mutate(raw)
    p = tmp_path / "plan.yaml"
    p.write_text(yaml.safe_dump(raw))
    return lint_plan(load_plan(p))


def test_lint_catches_duplicate_address(tmp_path):
    def m(raw):
        raw["routers"]["R3"]["interfaces"]["GigabitEthernet0/0"]["ipv4"] = "10.12.10.1/24"
    assert any("Duplicate address 10.12.10.1" in e for e in _broken(tmp_path, m))


def test_lint_catches_missing_ipv6(tmp_path):
    def m(raw):
        del raw["routers"]["R2"]["interfaces"]["Serial0/0/1"]["ipv6"]
    assert any("not dual-stack" in e for e in _broken(tmp_path, m))


def test_lint_catches_p2p_subnet_mismatch(tmp_path):
    def m(raw):
        raw["routers"]["R2"]["interfaces"]["Serial0/0/0"]["ipv4"] = "10.12.255.13/30"
    assert any("different IPv4 subnets" in e for e in _broken(tmp_path, m))


def test_render_respects_platform_capabilities(tmp_path):
    plan = load_plan(PLAN_FILE)
    pt = (render_configs(plan, "pt", tmp_path / "pt")[0]).read_text()
    lab = (render_configs(plan, "lab", tmp_path / "lab")[0]).read_text()
    assert "ipv6 unicast-routing" in pt
    assert "! skipped" in pt and "\nipv6 route 2001:db8:12::/48 Null0" not in pt
    assert "\nipv6 route 2001:db8:12::/48 Null0" in lab
    assert "ipv6 access-class MGMT6-ONLY in" in lab and "ipv6 access-class" not in pt


def test_lint_requires_exactly_one_dce_per_serial_link(tmp_path):
    def both(raw):
        raw["routers"]["R2"]["interfaces"]["Serial0/0/0"]["clock_rate"] = 64000
    def none(raw):
        del raw["routers"]["R1"]["interfaces"]["Serial0/0/0"]["clock_rate"]
    assert any("exactly one DCE" in e and "(found 2)" in e for e in _broken(tmp_path, both))
    assert any("exactly one DCE" in e and "(found 0)" in e for e in _broken(tmp_path, none))


def test_render_serial_and_management(tmp_path):
    plan = load_plan(PLAN_FILE)
    r1 = render_configs(plan, "pt", tmp_path)[0].read_text()
    r2 = (tmp_path / "R2.cfg").read_text()
    assert "interface Serial0/0/0" in r1 and r1.count("clock rate 64000") == 2
    assert r2.count("clock rate 64000") == 1            # R2 is DCE only toward R3
    assert "permit host 10.12.10.100" in r1             # SSH only from the automation server
    assert "access-class MGMT-ONLY in" in r1 and "transport input ssh" in r1
