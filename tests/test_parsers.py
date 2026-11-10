from pathlib import Path

from netauto.parsers import (
    facts_from_transcript, match_command, normalize_ifname, parse_ipv6_route,
)

ROOT = Path(__file__).resolve().parents[1]
SNAP = ROOT / "snapshots" / "pt"
FIX = ROOT / "tests" / "fixtures"


def facts(name):
    return facts_from_transcript((SNAP / f"{name}.txt").read_text(encoding="utf-8"))


def test_command_matching_accepts_abbreviations_and_rejects_typos():
    assert match_command("show ip interface brief") == "ip_int_brief"
    assert match_command("sh ip int br") == "ip_int_brief"
    assert match_command("sh ipv6 ospf nei") == "ospfv3_neighbors"
    assert match_command("how ipv6 route") is None          # real typo from the lab


def test_interface_names_are_normalized():
    assert normalize_ifname("Gi0/1") == "GigabitEthernet0/1"
    assert normalize_ifname("gigabitethernet0/1") == "GigabitEthernet0/1"
    assert normalize_ifname("Lo0") == "Loopback0"


def test_session_split_marks_failed_and_unknown_commands():
    # first capture from the lab: "terminal length 0" + a typo in "show ipv6 route"
    f, session = facts_from_transcript(
        (FIX / "R1_first_capture.txt").read_text(encoding="utf-8"))
    assert session.hostname == "R1"
    assert "terminal length 0" in session.unrecognized     # not supported in PT
    assert "how ipv6 route" in session.unrecognized
    assert f["ipv6_route"] is None                          # never captured


def test_output_pasted_after_bare_prompt_is_recognized():
    from netauto.parsers import split_session
    text = "R1#show ip route\nold\nR1#R1#show ip route\nnew line\nR1#"
    s = split_session(text)
    assert s.outputs["ip_route"].text.strip() == "new line"   # newest run wins
    assert s.unrecognized == []


def test_ip_interface_brief():
    f, _ = facts("R1")
    v4 = f["ip_int_brief"]
    assert v4["GigabitEthernet0/1"] == {"ipv4": "10.12.255.1", "status": "up", "protocol": "up"}
    assert v4["Vlan1"]["ipv4"] is None
    assert v4["Vlan1"]["status"] == "administratively down"


def test_ipv6_interface_brief_separates_link_local_and_global():
    f, _ = facts("R1")
    gi0 = f["ipv6_int_brief"]["GigabitEthernet0/0"]
    assert gi0["link_local"] == "fe80::1"
    assert gi0["global"] == ["2001:db8:12:10::1"]


def test_ip_route_ecmp_and_truncation_detection():
    f, _ = facts_from_transcript((FIX / "R1_first_capture.txt").read_text(encoding="utf-8"))
    assert f["ip_route"].expected_count == 13 and len(f["ip_route"].routes) == 11
    assert f["ip_route"].complete is False                  # copy was cut short
    f, _ = facts("R1")
    rt = f["ip_route"]
    ecmp = rt.routes["10.12.255.8/30"]
    assert ecmp.protocol == "O"
    assert ecmp.next_hops == ["10.12.255.2", "10.12.255.6"]
    assert rt.complete is True


def test_ip_route_static_null0():
    f, _ = facts("R1")
    s = f["ip_route"].routes["10.12.0.0/16"]
    assert s.protocol == "S" and s.interfaces == ["Null0"]


def test_ipv6_route_parser_on_pt_format():
    rt = parse_ipv6_route((FIX / "ipv6_route_R1_synthetic.txt").read_text())
    assert rt.expected_count == 11 and rt.complete
    assert rt.routes["2001:db8:12:20::/64"].next_hops == ["fe80::2"]
    assert rt.routes["2001:db8:12:ff23::/64"].interfaces == [
        "GigabitEthernet0/1", "GigabitEthernet0/2"]
    assert rt.routes["2001:db8:12:10::/64"].protocol == "C"
    assert rt.routes["2001:db8:12::1/128"].protocol == "LC"


def test_ospf_neighbors_v2_and_v3():
    f, _ = facts("R3")
    for key in ("ospf_neighbors", "ospfv3_neighbors"):
        ids = {(n["router_id"], n["interface"], n["state"]) for n in f[key]}
        assert ids == {("10.12.0.1", "GigabitEthernet0/1", "FULL"),
                       ("10.12.0.2", "GigabitEthernet0/2", "FULL")}


def test_truncated_table_is_repaired_with_show_ip_route_connected():
    f, _ = facts("R2")                     # full table shows 10 of 12 entries
    rt = f["ip_route"]
    assert rt.routes["10.12.255.8/30"].protocol == "C"
    assert rt.routes["10.12.255.8/30"].interfaces == ["GigabitEthernet0/2"]
    assert rt.complete is True
