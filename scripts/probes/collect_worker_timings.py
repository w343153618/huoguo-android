#!/usr/bin/env python3
"""Copy only numeric host timing samples; never export an entire runtime log."""
import argparse
import json
import math
from pathlib import Path


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def sanitize(row, since, until=None):
    stamp = row.get('unix_ms')
    if not number(stamp) or stamp < since or (until is not None and stamp > until):
        return None
    event = row.get('event')
    clean = {'event': event, 'unix_ms': stamp}
    if event == 'pipeline_sample':
        for key in ('interval_ms', 'fps_cap', 'raw_fps', 'submitted_fps', 'replaced_fps', 'idle_repeats'):
            if number(row.get(key)):
                clean[key] = row[key]
    elif event == 'phase_sample':
        clean['timings'] = {}
        timings = row.get('timings', {})
        if not isinstance(timings, dict):
            return None
        for key in ('capture_age', 'capture_gap', 'source_pts_gap', 'raw_queue', 'raw_pipe', 'encoded_egress'):
            source = timings.get(key)
            if isinstance(source, dict):
                clean['timings'][key] = {name: source[name] for name in ('count', 'p50_ms', 'p95_ms', 'max_ms')
                                        if number(source.get(name))}
        if row.get('raw_queue_policy') in ('fifo', 'latest'):
            clean['raw_queue_policy'] = row['raw_queue_policy']
    else:
        return None
    return clean


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log', type=Path, required=True, help='existing external runtime hardware/worker.log')
    parser.add_argument('--since-unix-ms', type=int, required=True)
    parser.add_argument('--until-unix-ms', type=int, help='exclude a subsequent bitrate/session window')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows = []
    with args.log.open('rb') as stream:
        stream.seek(max(0, args.log.stat().st_size - 1024 * 1024))
        for line in stream.read().decode(errors='replace').splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                clean = sanitize(row, args.since_unix_ms, args.until_unix_ms)
                if clean:
                    rows.append(clean)
    report = {'scope': 'Host capture/encoder pipe and encoded socket-write timings; not WAN transit or phone presentation.',
              'since_unix_ms': args.since_unix_ms, 'until_unix_ms': args.until_unix_ms, 'samples': rows,
              'limitations': 'Bounded final 1 MiB log read. Samples from simultaneous sessions cannot be separated.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'sample_count': len(rows), 'output': str(args.output)}))


if __name__ == '__main__':
    main()
