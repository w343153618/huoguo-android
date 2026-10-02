#!/usr/bin/env python3
"""Bounded numeric M1 resource context; never retains command lines or logs.

ps CPU percentages are OS estimates, not per-frame execution timings. They
cannot attribute a specific source-frame pause to CPU contention by themselves.
"""
import argparse
import json
import math
from pathlib import Path
import subprocess
import time

NAMES = {'qemu-system-aarch64-headless', 'qemu-system-aarch64',
         'WindowServer', 'JumpConnect', 'emulator_hardware_encoder',
         'huoguo-capture-trace-encoder', 'h264_udp_packetizer',
         'Python', 'python3', 'tailscaled'}


def collect():
    before = time.monotonic_ns()
    result = subprocess.run(['ps', '-axo', 'pid,pcpu,pmem,comm'],
                            capture_output=True, text=True, timeout=5)
    rows = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.split(None, 3)
        if len(parts) != 4 or Path(parts[3]).name not in NAMES:
            continue
        try:
            pid, cpu, mem = int(parts[0]), float(parts[1]), float(parts[2])
        except ValueError:
            continue
        if pid > 0 and all(math.isfinite(x) and x >= 0 for x in (cpu, mem)):
            rows.append(dict(pid=pid, executable_basename=Path(parts[3]).name,
                             cpu_percent=cpu, memory_percent=mem))
    return dict(host_before_monotonic_ns=before,
                host_after_monotonic_ns=time.monotonic_ns(),
                command_ok=result.returncode == 0, processes=rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, choices=range(10, 61), default=40)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('existing evidence is retained')
    rows, started = [], time.monotonic_ns()
    while (time.monotonic_ns()-started)/1e9 < args.seconds:
        rows.append(collect())
        time.sleep(1)
    logical = subprocess.run(['sysctl', '-n', 'hw.logicalcpu'],
                             capture_output=True, text=True, timeout=5)
    report = dict(schema=1, scope='Host resource context only; not causal per-frame proof',
                  logical_cpus=int(logical.stdout.strip()) if logical.stdout.strip().isdigit() else None,
                  host_started_monotonic_ns=started, host_ended_monotonic_ns=time.monotonic_ns(),
                  samples=rows,
                  limitations=['CPU percent may exceed 100 for multithreaded processes',
                               'ps CPU percent is an OS estimate, not a frame-specific scheduling trace',
                               'Only a fixed allowlist of executable basenames is retained'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(dict(samples=len(rows), logical_cpus=report['logical_cpus'])))


if __name__ == '__main__':
    main()
