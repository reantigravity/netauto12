#!/usr/bin/env python3
"""First contact with a real router: can Python reach it over SSH via Netmiko?

Connection data comes from environment variables (never from the repo):
    NETAUTO_HOST    e.g. devnetsandboxiosxec8k.cisco.com
    NETAUTO_PORT    default 22
    NETAUTO_USER    username from the sandbox "Quick Access" tab
    NETAUTO_SECRET  password from the sandbox "Quick Access" tab

Windows (cmd):
    set NETAUTO_HOST=devnetsandboxiosxec8k.cisco.com
    set NETAUTO_USER=...
    set NETAUTO_SECRET=...
    python tools\\ssh_check.py

Read-only: only "show" commands are sent.
"""

import os
import sys
import time

try:
    from netmiko import ConnectHandler
    from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException
except ImportError:
    sys.exit("Netmiko is not installed: pip install -r requirements.txt")


def main() -> int:
    missing = [v for v in ("NETAUTO_HOST", "NETAUTO_USER", "NETAUTO_SECRET") if not os.environ.get(v)]
    if missing:
        print("Set these environment variables first: " + ", ".join(missing))
        return 2
    device = {
        "device_type": "cisco_xe",
        "host": os.environ["NETAUTO_HOST"],
        "port": int(os.environ.get("NETAUTO_PORT", "22")),
        "username": os.environ["NETAUTO_USER"],
        "password": os.environ["NETAUTO_SECRET"],
        "conn_timeout": 20,
    }
    print(f"Connecting to {device['host']}:{device['port']} as {device['username']} ...")
    t0 = time.perf_counter()
    try:
        with ConnectHandler(**device) as conn:
            print(f"[ OK ] SSH session up in {time.perf_counter() - t0:.1f} s, "
                  f"prompt: {conn.find_prompt()}")
            for cmd in ("show version | include Software|uptime",
                        "show ip interface brief",
                        "show ipv6 interface brief"):
                print(f"\n### {cmd}")
                print(conn.send_command(cmd))
    except NetmikoAuthenticationException:
        print("[FAIL] Wrong username or password (sandbox reservation expired?)")
        return 1
    except NetmikoTimeoutException:
        print("[FAIL] Timeout: host unreachable, wrong port, or the sandbox is not running")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
