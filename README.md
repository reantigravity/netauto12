# netauto12 — Dual-stack configuration automation (CSS 312, Topic 12)

Python tooling that deploys IPv4/IPv6 addressing and routing to Cisco routers
and checks that the network matches the plan (compliance validation).

```
intended/network.yaml  --(lint)-->  templates/router.j2  -->  configs/*.cfg
        (plan)                            (Jinja2)               |
                                                                 v
                                     Netmiko (SSH) / NETCONF (YANG)  -->  routers
                                                                 |
                                       validate.py  <--  show commands
```

## Topology
Triangle R1 (HQ) – R2 (Branch-1) – R3 (Branch-2), Cisco 2911.
Each router has one LAN on Gi0/0 with a switch and a PC.
IPv4 block `10.12.0.0/16`, IPv6 block `2001:db8:12::/48`, OSPFv2 + OSPFv3 area 0.
Full table: `docs/addressing_table.md`.

## Usage
```bash
pip install -r requirements.txt
python generate.py --check          # lint the plan only
python generate.py                  # configs/pt/*.cfg  (paste into Packet Tracer)
python generate.py --target lab     # configs/lab/*.cfg (adds SSH for Netmiko)
```
Compliance validation (offline, from CLI transcripts copied out of Packet Tracer):
```bash
python validate.py --offline snapshots/pt --platform pt          # problems only
python validate.py --offline snapshots/pt --platform pt --all    # every check
python validate.py --offline snapshots/pt --csv reports/compliance.csv
python -m pytest                                                  # 18 tests
```
Each snapshot file is a CLI session with these commands:
`show ip interface brief`, `show ipv6 interface brief`, `show ip route`,
`show ipv6 route`, `show ip ospf neighbor`, `show ipv6 ospf neighbor`.

Credentials are read from `NETAUTO_USER` / `NETAUTO_SECRET` environment variables,
never stored in the repository.

## Plan checks (pre-deployment)
- every routed interface is dual-stack (IPv4 and IPv6)
- addresses are inside the project blocks, no duplicates, no network/broadcast
- both ends of each point-to-point link are in the same subnet and point to each other
- different links do not overlap
- hosts are in the LAN of the port they are cabled to, gateways match the router

## Compliance checks (post-deployment)
| Category | Check | IPv4 | IPv6 |
|---|---|:-:|:-:|
| Data | output captured, routing table not truncated (header count vs parsed) | x | x |
| Addressing | address and prefix length match the plan, link-local matches, no unplanned addresses | x | x |
| State | every planned interface is up/up | x | x |
| Dual-stack | every interface with IPv4 also has a global IPv6 address | x | x |
| OSPF | expected OSPFv2/OSPFv3 neighbor on each link, state FULL, no unexpected neighbors | x | x |
| Routing | route to every planned prefix, connected (C) locally and OSPF (O) remotely | x | x |
| Routing | planned static routes present (platform-aware: PT has no IPv6 Null0) | x | x |

Status: PASS / FAIL / WARN (cannot confirm, or not supported on platform) / SKIP (no data).
Exit code 1 if any FAIL, so the validator can gate a deployment pipeline.

## Roadmap
- [x] Stage 1: plan, lint, Jinja2 config generation, addressing tables
- [x] Stage 2: Packet Tracer topology built from the generated configs
- [ ] Stage 3: `deploy_netmiko.py` — push configs over SSH, backup, dry-run diff
- [x] Stage 4a: `validate.py` — compliance checks for IPv4 and IPv6 (offline snapshots)
- [ ] Stage 4b: live collection over SSH (Netmiko) feeding the same validator
- [ ] Stage 5: `deploy_netconf.py` — same intent via NETCONF/YANG (IOS XE)
- [ ] Stage 6: HTML report, tests, presentation
