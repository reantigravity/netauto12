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

## Status
- **Done:** plan + lint, Jinja2 config generation, Packet Tracer topology, offline compliance validator (157 PASS / 0 FAIL on the lab), drift demo, 22 tests.
- **Done (teacher feedback 19 Nov):** routers connected by serial WAN links (plan v2), automation server NETAUTO-SRV added to the Packet Tracer topology. Verified: OSPFv2/OSPFv3 FULL over serial, ping IPv4/IPv6 from NETAUTO-SRV to all loopbacks, SSH works from NETAUTO-SRV and is refused from PC2 (ACL MGMT-ONLY).
- **Note:** the SSH user password is not stored in the .pkt or in the repo; set it on each router with `username netauto privilege 15 secret <password>`.
- **Next:** Netmiko deploy over SSH on Cisco Modeling Labs Free, then NETCONF/YANG, HTML report, presentation.
- Demo script for the teacher: [`docs/DEMO.md`](docs/DEMO.md)
- Packet Tracer topology: [`topology/Kenzhaly_Topology.pkt`](topology/Kenzhaly_Topology.pkt) (Packet Tracer 9)

## Topology (plan v2)
Triangle R1 (HQ) – R2 (Branch-1) – R3 (Branch-2), Cisco 2911 with HWIC-2T.
Routers are connected by **serial WAN links** (Serial0/0/0, Serial0/0/1, clock rate 64000 on the DCE end).
Each router has one LAN on Gi0/0 with a switch and a PC.
IPv4 block `10.12.0.0/16`, IPv6 block `2001:db8:12::/48`, OSPFv2 + OSPFv3 area 0.
Full table: `docs/addressing_table.md`.

| Serial link | DCE (clock rate) | DTE | IPv4 | IPv6 |
|---|---|---|---|---|
| R1–R2 | R1 S0/0/0 | R2 S0/0/0 | 10.12.255.0/30 | 2001:db8:12:ff12::/64 |
| R1–R3 | R1 S0/0/1 | R3 S0/0/0 | 10.12.255.4/30 | 2001:db8:12:ff13::/64 |
| R2–R3 | R2 S0/0/1 | R3 S0/0/1 | 10.12.255.8/30 | 2001:db8:12:ff23::/64 |

Plan v1 (Ethernet links between routers) is kept in `intended/archive/` together with
its Packet Tracer snapshots `snapshots/pt_v1_ethernet/`.

## Where the Python code runs
The scripts do **not** run on routers or switches. They live and run on a dedicated
**automation server `NETAUTO-SRV`** (10.12.10.100, 2001:db8:12:10::100) in the HQ LAN —
in a real company this is an admin workstation or a server ("control node").

```
 NETAUTO-SRV (Python, Netmiko, ncclient, network.yaml)
      |  SSH (TCP 22) / NETCONF (TCP 830)
      v
  SW1 -- R1 ==serial== R2
          \\          //
           == R3 ===
```
- Routers keep only their configuration; the logic (plan, templates, validation) is on the server.
- Routers accept SSH **only from NETAUTO-SRV** (`access-class MGMT-ONLY` on the VTY lines),
  so nobody else can log in to change the network.
- One server manages any number of devices: adding a router means adding it to `network.yaml`.

## Usage
```bash
pip install -r requirements.txt
python generate.py --check          # lint the plan only
python generate.py                  # configs/pt/*.cfg  (paste into Packet Tracer)
python generate.py --target lab     # configs/lab/*.cfg (adds SSH for Netmiko)
```
Compliance validation (offline, from CLI transcripts copied out of Packet Tracer):
```bash
python validate.py --plan intended/archive/network_v1_ethernet.yaml --offline snapshots/pt_v1_ethernet --platform pt   # v1 snapshots
python -m pytest                                                  # 22 tests
```
Each snapshot file is a CLI session with these commands:
`show ip interface brief`, `show ipv6 interface brief`, `show ip route`,
`show ipv6 route`, `show ip ospf neighbor`, `show ipv6 ospf neighbor`
(optional: `show ip route connected` — used to repair a truncated `show ip route`).

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
