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
Credentials are read from `NETAUTO_USER` / `NETAUTO_SECRET` environment variables,
never stored in the repository.

## Plan checks (pre-deployment)
- every routed interface is dual-stack (IPv4 and IPv6)
- addresses are inside the project blocks, no duplicates, no network/broadcast
- both ends of each point-to-point link are in the same subnet and point to each other
- different links do not overlap
- hosts are in the LAN of the port they are cabled to, gateways match the router

## Roadmap
- [x] Stage 1: plan, lint, Jinja2 config generation, addressing tables
- [ ] Stage 2: Packet Tracer topology built from the generated configs
- [ ] Stage 3: `deploy_netmiko.py` — push configs over SSH, backup, dry-run diff
- [ ] Stage 4: `validate.py` — compliance checks for IPv4 and IPv6 (live + offline)
- [ ] Stage 5: `deploy_netconf.py` — same intent via NETCONF/YANG (IOS XE)
- [ ] Stage 6: HTML report, tests, presentation
