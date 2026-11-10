"""What each target platform can and cannot do.

The intended plan is the same everywhere; the config generator and the
validator adapt to the platform instead of the plan being changed.
"""

CAPABILITIES = {
    # Packet Tracer 9 (Cisco 2911)
    "pt": {
        "ipv6_null_route": False,     # rejects "ipv6 route <prefix> Null0"
        "terminal_length": False,     # rejects "terminal length 0"
    },
    # Real IOS / IOS XE (CML, DevNet Sandbox)
    "lab": {
        "ipv6_null_route": True,
        "terminal_length": True,
    },
}
