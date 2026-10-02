#!/usr/bin/env python3
"""Pair an isolated native clock reader with Python; no network or device use."""
import argparse
import hashlib
import json
from pathlib import Path
import select
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / 'experiments/moonlight-v2/transport/android-udp'
PROGRAM = r'''
#include "host_clock.hpp"
#include <iostream>
#include <string>
int main() {
    std::string request;
    while (std::getline(std::cin, request)) {
        const auto value = huoguo::android_udp::hostMonotonicUs();
        std::cout << "{\"native_us\":" << value << ",\"clock_domain\":\""
                  << huoguo::android_udp::HostClockDomain << "\"}\n" << std::flush;
    }
}
'''


def verify(samples=16, compiler='c++'):
    if type(samples) is not int or not 1 <= samples <= 64:
        raise ValueError('clock sample bound')
    rows = []
    with tempfile.TemporaryDirectory(prefix='huoguo-host-clock-') as folder:
        source = Path(folder) / 'clock.cpp'
        binary = Path(folder) / 'clock'
        source.write_text(PROGRAM)
        subprocess.run([compiler, '-std=c++20', '-Wall', '-Wextra', '-Werror',
                        '-I', str(DIRECTORY), str(source), '-o', str(binary)],
                       check=True, capture_output=True, timeout=30)
        process = subprocess.Popen([str(binary)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, text=True, bufsize=1)
        try:
            for index in range(samples):
                before = time.monotonic_ns()
                process.stdin.write('sample\n')
                process.stdin.flush()
                if not select.select([process.stdout], [], [], 2)[0]:
                    raise TimeoutError('bounded native clock response')
                row = json.loads(process.stdout.readline())
                after = time.monotonic_ns()
                lower = before // 1000
                upper = (after + 999) // 1000
                row.update(sample=index, python_before_ns=before, python_after_ns=after,
                           bracket_width_ns=after-before,
                           native_inside_python_bracket=lower <= row['native_us'] <= upper)
                rows.append(row)
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            process.stdout.close()
    return dict(schema='native-host-clock-bracket-v1',
                scope='isolated_local_clock_API_no_socket_device_encoder_or_live_service',
                python_clock_implementation=time.get_clock_info('monotonic').implementation,
                native_header_sha256=hashlib.sha256((DIRECTORY/'host_clock.hpp').read_bytes()).hexdigest(),
                sample_count=len(rows), all_inside_python_bracket=all(
                    row['native_inside_python_bracket'] for row in rows), samples=rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=int, default=16)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = verify(args.samples)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + '\n')
    else:
        print(json.dumps(result))
    return 0 if result['all_inside_python_bracket'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
