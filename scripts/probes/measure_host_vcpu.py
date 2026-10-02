#!/usr/bin/env python3
"""Bounded, read-only macOS QEMU thread CPU/state sampling.

Example (the PID must be independently verified as the intended emulator):
  python3 scripts/probes/measure_host_vcpu.py --pid 123 --seconds 30 \
      --interval-ms 25 --output /private/tmp/host-vcpu.json

No debugger, task port, stack, command line, environment, or device access is
used. Ownership and process start time are checked around each sample. A fixed
thread set is selected at startup; arbitrary thread names and executable paths
are used transiently for guards and are never serialized. Unknown names select
the busiest startup CPU candidates, with vcpu_identity_unknown=1 throughout.

All report values are numeric. role_id 1000..1063 means an exact CPU n[/HVF,
TCG,KVM] name match; 2000..2015 means an unidentified CPU candidate ranked by
startup CPU delta; 3000..3015 means a caller-supplied unique ID whose vCPU role
was checked outside this probe (for example cpu_thread_fn + hvf_vcpu_exec in a
private sample call graph). --vcpu-thread-id accepts those IDs; their guest
core index and completeness are unknown, and the probe does not read stacks.
PROC_PIDLISTTHREADS returns pthread handles for flavor 5,
not the unique thread IDs accepted by flavor 15. run_state is the SDK value:
0 unknown, 1 running/runnable, 2 stopped, 3 waiting, 4 uninterruptible, 5 halted.

This is sampling, not a complete schedule trace. State 1 does not establish
that a CPU core was executing the thread at the read instant. Zero CPU delta
does not explain why a thread did not execute. Neither positive CPU deltas nor
states at sample endpoints exclude pauses between reads. All default host
timestamps explicitly use clock_gettime_ns(CLOCK_MONOTONIC), identified by
host_clock_code=1. Python's monotonic_ns() can have a different clock origin
on macOS and must not be substituted. Guest event association still needs a
separately measured guest-to-host clock alignment.

The ABI and nanosecond CPU units follow the local macOS SDK and Apple XNU:
https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/kern/bsd_kern.c
https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/kern/thread.c
"""

import argparse
import ctypes
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import sys
import time


MAX_DISCOVERY_THREADS = 128
MAX_SAMPLED_THREADS = 16
QEMU_BASENAMES = {b'qemu-system-aarch64', b'qemu-system-aarch64-headless'}
VCPU_NAME = re.compile(rb'CPU ([0-9]{1,2})(?:/(?:HVF|TCG|KVM))?')

# Fixed status values; exceptions and OS messages are never printed.
OK, PLATFORM, LIBPROC, PROCESS_READ, OWNER, EXECUTABLE = range(6)
LIST_LIMIT, THREAD_READ, IDENTITY_CHANGED, COUNTER_REGRESSED = range(6, 10)
OUTPUT, OPTIONS, NO_THREADS, INTERRUPTED = range(10, 14)


def host_clock_ns():
    """The exact CLOCK_MONOTONIC domain used by the guest fence clock model."""
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC)


class ProbeError(Exception):
    def __init__(self, code, errno=0):
        self.code, self.errno = int(code), int(errno)
        super().__init__(self.code)


class ProcBsdInfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in (
        'flags', 'status', 'xstatus', 'pid', 'ppid', 'uid', 'gid', 'ruid',
        'rgid', 'svuid', 'svgid', 'reserved')]
    _fields_ += [('comm', ctypes.c_char * 16), ('name', ctypes.c_char * 32)]
    _fields_ += [(name, ctypes.c_uint32) for name in (
        'nfiles', 'pgid', 'pjobc', 'tdev', 'tpgid')]
    _fields_ += [('nice', ctypes.c_int32), ('start_sec', ctypes.c_uint64),
                ('start_usec', ctypes.c_uint64)]


class ProcThreadInfo(ctypes.Structure):
    _fields_ = [('user_ns', ctypes.c_uint64), ('system_ns', ctypes.c_uint64)]
    _fields_ += [(name, ctypes.c_int32) for name in (
        'cpu_usage', 'policy', 'run_state', 'flags', 'sleep_time',
        'curpri', 'priority', 'maxpriority')]
    _fields_ += [('name', ctypes.c_char * 64)]


@dataclass(frozen=True)
class Identity:
    pid: int
    uid: int
    ruid: int
    svuid: int
    start_sec: int
    start_usec: int
    comm: bytes
    name: bytes


@dataclass(frozen=True)
class ThreadRead:
    before_ns: int
    after_ns: int
    user_ns: int
    system_ns: int
    run_state: int
    flags: int
    cpu_usage: int
    name: bytes


def vcpu_index(name):
    match = VCPU_NAME.fullmatch(name)
    index = int(match.group(1)) if match else -1
    return index if 0 <= index < 64 else -1


class LibProc:
    def __init__(self, clock=host_clock_ns):
        if sys.platform != 'darwin':
            raise ProbeError(PLATFORM)
        self.clock = clock
        try:
            self.lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        except OSError:
            raise ProbeError(LIBPROC) from None
        self.pidinfo = self.lib.proc_pidinfo
        self.pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                 ctypes.c_void_p, ctypes.c_int]
        self.pidinfo.restype = ctypes.c_int
        self.pidpath = self.lib.proc_pidpath
        self.pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        self.pidpath.restype = ctypes.c_int

    def _info(self, pid, flavor, arg, buffer, code):
        ctypes.set_errno(0)
        count = self.pidinfo(pid, flavor, arg, ctypes.byref(buffer),
                             ctypes.sizeof(buffer))
        if count != ctypes.sizeof(buffer):
            raise ProbeError(code, ctypes.get_errno())

    def identity(self, pid):
        value = ProcBsdInfo()
        self._info(pid, 3, 0, value, PROCESS_READ)
        return Identity(value.pid, value.uid, value.ruid, value.svuid,
                        value.start_sec, value.start_usec,
                        bytes(value.comm), bytes(value.name))

    def is_qemu(self, pid):
        value = ctypes.create_string_buffer(4096)
        ctypes.set_errno(0)
        count = self.pidpath(pid, value, ctypes.sizeof(value))
        if count <= 0 or count >= ctypes.sizeof(value):
            raise ProbeError(PROCESS_READ, ctypes.get_errno())
        return value.value.rsplit(b'/', 1)[-1] in QEMU_BASENAMES

    def threads(self, pid):
        values = (ctypes.c_uint64 * MAX_DISCOVERY_THREADS)()
        ctypes.set_errno(0)
        count = self.pidinfo(pid, 6, 0, ctypes.byref(values), ctypes.sizeof(values))
        if count <= 0 or count % 8 or count > ctypes.sizeof(values):
            raise ProbeError(THREAD_READ, ctypes.get_errno())
        # A full buffer might have omitted more threads. Do not silently rank
        # an incomplete process thread list.
        if count == ctypes.sizeof(values):
            raise ProbeError(LIST_LIMIT)
        handles = [int(values[i]) for i in range(count // 8)]
        if any(handle <= 0 for handle in handles) or len(set(handles)) != len(handles):
            raise ProbeError(THREAD_READ)
        return handles

    def thread(self, pid, handle, flavor=5):
        value = ProcThreadInfo()
        before = self.clock()
        self._info(pid, flavor, handle, value, THREAD_READ)
        after = self.clock()
        return ThreadRead(before, after, value.user_ns, value.system_ns,
                          value.run_state, value.flags, value.cpu_usage,
                          bytes(value.name))


def checked_identity(backend, pid, owner_uid, expected=None):
    value = backend.identity(pid)
    if value.pid != pid or (expected is not None and value != expected):
        raise ProbeError(IDENTITY_CHANGED)
    if any(uid != owner_uid for uid in (value.uid, value.ruid, value.svuid)):
        raise ProbeError(OWNER)
    return value


def cpu_delta(previous, current):
    user = current.user_ns - previous.user_ns
    system = current.system_ns - previous.system_ns
    if user < 0 or system < 0:
        raise ProbeError(COUNTER_REGRESSED)
    return user, system


def discover(backend, pid, identity, owner_uid, max_threads, sleep=time.sleep):
    handles = backend.threads(pid)
    first, second = {}, {}
    failures = 0
    for handle in handles:
        try:
            first[handle] = backend.thread(pid, handle)
        except ProbeError:
            failures += 1
    sleep(0.05)
    checked_identity(backend, pid, owner_uid, identity)
    for handle in first:
        try:
            value = backend.thread(pid, handle)
            cpu_delta(first[handle], value)
            if vcpu_index(first[handle].name) != vcpu_index(value.name):
                failures += 1
                continue
            second[handle] = value
        except ProbeError:
            failures += 1
    checked_identity(backend, pid, owner_uid, identity)
    if not second:
        raise ProbeError(NO_THREADS)
    known = [(vcpu_index(value.name), handle) for handle, value in second.items()
             if vcpu_index(value.name) >= 0]
    if len(known) > max_threads or len({index for index, _ in known}) != len(known):
        raise ProbeError(LIST_LIMIT)
    ranked = sorted(second, key=lambda handle: (
        -sum(cpu_delta(first[handle], second[handle])), handle))
    selected = sorted(known) if known else [(-1, handle) for handle in ranked[:max_threads]]
    roles = []
    for rank, (index, handle) in enumerate(selected):
        roles.append(dict(role_id=1000 + index if index >= 0 else 2000 + rank,
                          thread_handle=handle, thread_id64=0, query_flavor=5,
                          role_basis_code=1 if index >= 0 else 2,
                          vcpu_index=index,
                          vcpu_name_matched=int(index >= 0),
                          startup_cpu_delta_ns=sum(cpu_delta(first[handle], second[handle]))))
    context = dict(discovered_thread_count=len(handles),
                   discovery_read_failure_count=failures,
                   vcpu_name_match_count=len(known),
                   vcpu_identity_unknown=int(not known),
                   external_vcpu_role_asserted=0,
                   startup_ranking_used=int(not known),
                   sampled_thread_count=len(roles),
                   unselected_thread_count=len(handles) - len(roles))
    return roles, context


def capture(pid, seconds=30, interval_ms=25, max_threads=16, self_probe=False,
            backend=None, clock=host_clock_ns, sleep=time.sleep,
            owner_uid=None, vcpu_thread_ids=()):
    if (not isinstance(pid, int) or not 0 < pid < 2**31 or not math.isfinite(seconds)
            or not 2 <= seconds <= 60 or not isinstance(interval_ms, int)
            or not 20 <= interval_ms <= 50 or not isinstance(max_threads, int)
            or not 1 <= max_threads <= MAX_SAMPLED_THREADS
            or len(vcpu_thread_ids) > max_threads
            or len(set(vcpu_thread_ids)) != len(vcpu_thread_ids)
            or any(not isinstance(tid, int) or not 0 < tid < 2**64
                   for tid in vcpu_thread_ids)):
        raise ProbeError(OPTIONS)
    if self_probe and (pid != os.getpid() or vcpu_thread_ids):
        raise ProbeError(OPTIONS)
    backend = backend or LibProc(clock)
    owner_uid = os.getuid() if owner_uid is None else owner_uid
    started = clock()
    identity = checked_identity(backend, pid, owner_uid)
    if not self_probe and not backend.is_qemu(pid):
        raise ProbeError(EXECUTABLE)
    if vcpu_thread_ids:
        # Unique IDs are queried directly. Never guess a mapping from a sample
        # Thread_N header to a PROC_PIDLISTTHREADS pthread handle.
        roles = [dict(role_id=3000 + rank, thread_handle=0, thread_id64=tid,
                      query_flavor=15, role_basis_code=3, vcpu_index=-1,
                      vcpu_name_matched=0, startup_cpu_delta_ns=-1)
                 for rank, tid in enumerate(sorted(vcpu_thread_ids))]
        for role in roles:
            backend.thread(pid, role['thread_id64'], 15)
        checked_identity(backend, pid, owner_uid, identity)
        context = dict(discovered_thread_count=-1, discovery_read_failure_count=0,
                       vcpu_name_match_count=0, vcpu_identity_unknown=0,
                       external_vcpu_role_asserted=1, startup_ranking_used=0,
                       sampled_thread_count=len(roles), unselected_thread_count=-1)
    else:
        roles, context = discover(backend, pid, identity, owner_uid, max_threads, sleep)
    sampling_started = clock()
    interval_ns = int(interval_ms * 1_000_000)
    deadline = sampling_started + int(seconds * 1_000_000_000)
    due = sampling_started
    samples, previous = [], {}
    status, failure_errno, skipped = OK, 0, 0
    sampler_cpu_started = time.process_time_ns()
    while clock() < deadline:
        wait_ns = due - clock()
        if wait_ns > 0:
            sleep(wait_ns / 1e9)
        if clock() >= deadline:
            break
        before = clock()
        rows = []
        identity_validated = 0
        try:
            checked_identity(backend, pid, owner_uid, identity)
            for role in roles:
                handle = role['thread_id64'] or role['thread_handle']
                read_before = clock()
                try:
                    value = backend.thread(pid, handle, role['query_flavor'])
                    if role['query_flavor'] == 5 and vcpu_index(value.name) != role['vcpu_index']:
                        raise ProbeError(IDENTITY_CHANGED)
                    delta_user, delta_system = cpu_delta(previous[handle], value) if handle in previous else (-1, -1)
                    previous_value = previous.get(handle)
                    elapsed = ((value.before_ns + value.after_ns - previous_value.before_ns - previous_value.after_ns) // 2
                               if previous_value else -1)
                    rows.append(dict(role_id=role['role_id'], available=1, error_code=0,
                                     error_errno=0, host_before_monotonic_ns=value.before_ns,
                                     host_after_monotonic_ns=value.after_ns,
                                     run_state=value.run_state, flags=value.flags,
                                     cpu_usage_scaled=value.cpu_usage,
                                     user_ns=value.user_ns, system_ns=value.system_ns,
                                     delta_user_ns=delta_user, delta_system_ns=delta_system,
                                     delta_elapsed_ns=elapsed))
                    previous[handle] = value
                except ProbeError as error:
                    rows.append(dict(role_id=role['role_id'], available=0,
                                     error_code=error.code, error_errno=error.errno,
                                     host_before_monotonic_ns=read_before,
                                     host_after_monotonic_ns=clock(),
                                     run_state=-1, flags=-1, cpu_usage_scaled=-1,
                                     user_ns=-1, system_ns=-1, delta_user_ns=-1,
                                     delta_system_ns=-1, delta_elapsed_ns=-1))
                    previous.pop(handle, None)
                    # A lost/reused handle cannot be followed as the same role.
                    status, failure_errno = error.code, error.errno
                    break
            checked_identity(backend, pid, owner_uid, identity)
            identity_validated = 1
        except ProbeError as error:
            status, failure_errno = error.code, error.errno
        after = clock()
        samples.append(dict(sample_index=len(samples),
                            host_due_monotonic_ns=due,
                            host_before_monotonic_ns=before,
                            host_after_monotonic_ns=after,
                            start_lateness_ns=max(0, before - due),
                            sample_read_duration_ns=after - before,
                            identity_validated=identity_validated,
                            complete_thread_set_read=int(len(rows) == len(roles)
                                                        and all(row['available'] for row in rows)),
                            threads=rows))
        if status != OK:
            break
        due += interval_ns
        now = clock()
        if due <= now:
            missed = (now - due) // interval_ns + 1
            skipped += missed
            due += missed * interval_ns
    ended = clock()
    durations = sorted(sample['sample_read_duration_ns'] for sample in samples)
    starts = [sample['host_before_monotonic_ns'] for sample in samples]
    gaps = sorted(right - left for left, right in zip(starts, starts[1:]))
    return dict(schema=1, host_clock_code=1, scope_code=2 if self_probe else 1, pid=pid,
                status_code=status, error_errno=failure_errno,
                pid_start_sec=identity.start_sec, pid_start_usec=identity.start_usec,
                owner_matches=1,
                host_started_monotonic_ns=started,
                host_sampling_started_monotonic_ns=sampling_started,
                host_ended_monotonic_ns=ended,
                requested_duration_ns=int(seconds * 1e9),
                requested_interval_ns=interval_ns,
                max_discovery_threads=MAX_DISCOVERY_THREADS,
                max_sampled_threads=max_threads,
                sampler_cpu_ns=time.process_time_ns() - sampler_cpu_started,
                samples_count=len(samples), skipped_slots=skipped,
                sample_read_median_ns=durations[len(durations) // 2] if durations else -1,
                sample_read_max_ns=max(durations) if durations else -1,
                sample_gap_median_ns=gaps[len(gaps) // 2] if gaps else -1,
                sample_gap_max_ns=max(gaps) if gaps else -1,
                discovery=context, roles=roles, samples=samples,
                limitations=dict(complete_schedule_trace=0,
                                 excludes_between_sample_pauses=0,
                                 run_state_proves_current_core_execution=0,
                                 zero_cpu_delta_proves_host_starvation=0,
                                 guest_clock_aligned=0,
                                 candidate_roles_prove_vcpu_identity=0,
                                 probe_performed_external_stack_validation=0,
                                 guest_vcpu_index_known=int(all(role['vcpu_index'] >= 0
                                                               for role in roles)),
                                 vcpu_set_completeness_known=0,
                                 thread_reads_are_simultaneous=0,
                                 frozen_startup_thread_set=1))


class NumericParser(argparse.ArgumentParser):
    def error(self, message):
        print(json.dumps(dict(status_code=OPTIONS, error_errno=0)))
        raise SystemExit(2)


def main(argv=None):
    parser = NumericParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--pid', type=int)
    target.add_argument('--self', action='store_true', dest='self_probe')
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--interval-ms', type=int, default=25)
    parser.add_argument('--max-threads', type=int, default=16)
    parser.add_argument('--vcpu-thread-id', type=int, nargs='+', default=[])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        # Exclusively reserve an output before reading a process. Never replace
        # existing evidence, including through an existing symlink.
        with args.output.open('x', encoding='utf-8') as output:
            os.fchmod(output.fileno(), 0o600)
            try:
                report = capture(os.getpid() if args.self_probe else args.pid,
                                 args.seconds, args.interval_ms, args.max_threads,
                                 args.self_probe, vcpu_thread_ids=args.vcpu_thread_id)
            except ProbeError as error:
                report = dict(schema=1, host_clock_code=1, status_code=error.code,
                              error_errno=error.errno)
            except KeyboardInterrupt:
                report = dict(schema=1, host_clock_code=1, status_code=INTERRUPTED, error_errno=0)
            json.dump(report, output, separators=(',', ':'))
            output.write('\n')
        print(json.dumps(dict(status_code=report['status_code'],
                              samples_count=report.get('samples_count', 0),
                              sampled_thread_count=report.get('discovery', {}).get('sampled_thread_count', 0),
                              vcpu_identity_unknown=report.get('discovery', {}).get('vcpu_identity_unknown', 1),
                              sample_read_max_ns=report.get('sample_read_max_ns', -1),
                              sample_gap_max_ns=report.get('sample_gap_max_ns', -1))))
        return int(report['status_code'] != OK)
    except ProbeError as error:
        print(json.dumps(dict(status_code=error.code, error_errno=error.errno)))
        return 1
    except OSError as error:
        print(json.dumps(dict(status_code=OUTPUT, error_errno=error.errno or 0)))
        return 1
    except KeyboardInterrupt:
        print(json.dumps(dict(status_code=INTERRUPTED, error_errno=0)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
