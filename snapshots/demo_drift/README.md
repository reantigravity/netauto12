# Demo: R2 changed by hand after deployment

R1 and R3 are the real Packet Tracer snapshots. R2 was "broken" on purpose:
- Gi0/2 shut down
- link-local on Gi0/0 changed (fe80::2 -> EUI-64)
- Vlan1 got an IPv4-only address (no IPv6)
- as a result OSPFv2/OSPFv3 neighbor R3 disappeared

Run: python validate.py --offline snapshots/demo_drift --platform pt
