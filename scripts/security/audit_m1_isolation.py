#!/usr/bin/env python3
"""Read-only host boundary audit; optional probes use only fresh owned listeners.

Never reads credentials, personal file contents, full process arguments or PF
rules. This does not deploy isolation, scan LAN peers or prove VM escape safety.
"""
from __future__ import annotations

import argparse
import datetime
import ipaddress
import json
import os
from pathlib import Path
import plistlib
import re
import secrets
import socket
import subprocess
import threading


LABELS = (
    "local.remoteandroid.m1compare.emulator",
    "local.remoteandroid.m1compare.gateway",
    "local.remoteandroid.download",
    "local.huoguo.m1.npc",
    "local.huoguo.m1.npc-relay",
)


def run(args: list[str], timeout: float = 8) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def interface_address(interface: str) -> str | None:
    if not re.fullmatch(r"en[0-9]+", interface):
        raise ValueError("Only a named local Ethernet/Wi-Fi interface is accepted")
    result = run(["/usr/sbin/ipconfig", "getifaddr", interface])
    if result.returncode:
        return None
    address = ipaddress.ip_address(result.stdout.strip())
    if address.version != 4 or address.is_loopback or address.is_unspecified:
        raise ValueError("Expected a local interface IPv4 address")
    return str(address)


def canary(adb: str, serial: str, bind: str, target: str) -> dict:
    """No target supplied by callers except loopback alias or owned bind IP.

    This deliberately tests TCP only. A denied result is not UDP/IPv6/SSRF
    acceptance and may also mean the guest or its nc tool was unavailable.
    """
    if not re.fullmatch(r"emulator-[0-9]+", serial):
        raise ValueError("Canary probes are limited to an explicitly named emulator")
    if (bind, target) != ("127.0.0.1", "10.0.2.2") and bind != target:
        raise ValueError("Probe targets must be fresh listeners on this host")
    ipaddress.IPv4Address(bind)
    ipaddress.IPv4Address(target)
    marker = ("huoguo-owned-canary-" + secrets.token_hex(16)).encode()
    accepted = threading.Event()
    stop = threading.Event()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((bind, 0))
    server.listen(1)
    server.settimeout(0.1)
    port = server.getsockname()[1]

    def serve() -> None:
        while not stop.is_set():
            try:
                client, _ = server.accept()
                with client:
                    client.settimeout(1)
                    client.sendall(marker + b"\n")
                accepted.set()
                return
            except socket.timeout:
                continue
            except OSError:
                return

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    result = {
        "case": "host_loopback_alias" if bind == "127.0.0.1" else "owned_host_lan_address",
        "guest_target": target,
        "transport": "TCP",
        "reachable": False,
    }
    try:
        proc = subprocess.run(
            [adb, "-s", serial, "shell", "toybox", "nc", "-w", "2", target, str(port)],
            input=b"", capture_output=True, timeout=6,
        )
        result.update(reachable=marker in proc.stdout, exit_code=proc.returncode)
    except subprocess.TimeoutExpired:
        result["timed_out"] = True
    finally:
        stop.set()
        server.close()
        worker.join(timeout=1)
    result["host_accepted"] = accepted.is_set()
    return result


def collect(home: Path, adb: str, serial: str, interface: str, probe: bool) -> dict:
    report = {
        "schema": 1,
        "time_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scope": "M1 metadata and optionally fresh host-owned TCP listeners only",
        "personal_uid": os.getuid(),
        "runtime_changed": False,
        "credentials_or_personal_contents_read": False,
        "host_isolation_accepted": False,
        "launchers": [], "processes": [], "canaries": [], "findings": [],
        "untested": ["UDP", "IPv6", "other LAN peers", "proxy bypass", "DNS rebinding",
                     "isolated-UID file access", "restart/interface switch", "VM escape"],
    }
    for label in LABELS:
        path = home / "Library/LaunchAgents" / (label + ".plist")
        if not path.is_file():
            continue
        config = plistlib.loads(path.read_bytes())
        args = config.get("ProgramArguments", [])
        report["launchers"].append({
            "label": label, "user_name": config.get("UserName"),
            "program": args[0] if args else config.get("Program"),
            "environment_keys": sorted(config.get("EnvironmentVariables", {})),
        })
    result = run(["/bin/ps", "-axo", "pid=,uid=,user=,comm="])
    if result.returncode:
        report["findings"].append("process_inventory_unavailable")
    else:
        for line in result.stdout.splitlines():
            match = re.match(r"\s*(\d+)\s+(\d+)\s+(\S+)\s+(.+)$", line)
            if not match:
                continue
            pid, uid, user, command = match.groups()
            if not ("qemu-system" in command or Path(command).name == "adb"):
                continue
            item = {"pid": int(pid), "uid": int(uid), "user": user, "program": command}
            report["processes"].append(item)
            if int(uid) == os.getuid():
                report["findings"].append("android_host_process_shares_personal_uid")
    if probe:
        state = run([adb, "-s", serial, "get-state"])
        if state.returncode or state.stdout.strip() != "device":
            report["findings"].append("named_guest_unavailable_canaries_skipped")
        else:
            report["canaries"].append(canary(adb, serial, "127.0.0.1", "10.0.2.2"))
            address = interface_address(interface)
            if address:
                report["canaries"].append(canary(adb, serial, address, address))
            else:
                report["findings"].append("physical_interface_address_unavailable")
            for item in report["canaries"]:
                if item["reachable"]:
                    report["findings"].append("guest_reaches_" + item["case"])
    report["findings"] = sorted(set(report["findings"]))
    report["boundary"] = "Blocked canaries alone never imply full host/network isolation"
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default=str(Path.home() / "Library/Android/sdk/platform-tools/adb"))
    parser.add_argument("--serial", default="emulator-5556")
    parser.add_argument("--interface", default="en7")
    parser.add_argument("--owned-canaries", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"emulator-[0-9]+", args.serial):
        parser.error("This audit only accepts an explicitly named emulator")
    print(json.dumps(collect(Path.home(), args.adb, args.serial, args.interface,
                             args.owned_canaries), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
