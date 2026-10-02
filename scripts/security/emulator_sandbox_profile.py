#!/usr/bin/env python3
"""Generate an EXPERIMENTAL Darwin Seatbelt profile, without installing it.

This is not App Sandbox, a supported production boundary, or an emulator/GPU/
Hypervisor acceptance result. ``sandbox-exec`` is deprecated on this host.
The default-allow starting point deliberately leaves unknown OS capabilities
unrestricted while feasibility is evaluated; files and sockets are constrained
explicitly. Do not publish an emulator using this candidate before testing all
required devices, child processes, management endpoints and negative canaries.

The installer must separately verify dedicated UIDs, root-owned immutable SDK/
code/profile/launcher, ownership and symlink-free paths immediately before exec,
restricted inherited descriptors, and PF/proxy destination enforcement. Merely
generating a profile neither checks ownership nor creates these directories.

SBPL syntax below is checked locally using a harmless /usr/bin/true child. Only
``localhost`` and ``*`` are accepted by this host's SBPL network-address parser;
numeric IP restrictions therefore belong in the guarded proxy/DNS service, not
in a pretend SBPL IP allowlist. DNS is OFF by default. Its explicit opt-in permits
only one localhost UDP port, for a separately isolated DNS guard. Public UDP53
is never permitted directly by this profile.

Examples (root-owned installer supplies paths and a guarded proxy port):
  python3 scripts/security/emulator_sandbox_profile.py --proxy-port 28970
  python3 scripts/security/emulator_sandbox_profile.py --proxy-port 28970 --check
No ADB client connection to port 5037, generic localhost, wildcard TCP port or
private-network exception is added. Optional inbound management listeners are
separately enumerated, never inferred from the proxy port.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys


ISOLATION_BASE = Path("/private/var/lib/huoguo-android-isolation")
HOMEBREW_CELLAR = Path("/opt/homebrew/Cellar")
# Do not allow all of /System: /System/Volumes/Data aliases user data.
OS_READ_ROOTS = ("/System/Library", "/System/Cryptexes",
                 "/System/Volumes/Preboot/Cryptexes/OS", "/usr/lib", "/usr/bin",
                 "/usr/sbin", "/bin", "/sbin")
OS_READ_FILES = ("/", "/dev/null", "/dev/random", "/dev/urandom", "/private/etc/hosts",
                 "/private/etc/resolv.conf", "/private/var/run/resolv.conf")
FORBIDDEN_ROOTS = ("/Users", "/Volumes", "/Network", "/var/root", "/private/var/root",
                   "/System/Volumes/Data/Users", "/System/Volumes/Data/Volumes",
                   "/System/Volumes/Data/Network", "/System/Volumes/Data/private/var/root")


def sbpl_string(value: str) -> str:
    """Quote data, never splice a caller-provided SBPL expression into code."""
    if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("SBPL strings must not contain control characters")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _path(value: str | Path) -> Path:
    raw = str(value)
    if not raw.startswith("/") or any(part in (".", "..") for part in raw.split("/")):
        raise ValueError("Paths must be absolute without dot or parent components")
    sbpl_string(raw)
    return Path(raw)


def _isolated_path(value: str | Path) -> Path:
    path = _path(value)
    if path == ISOLATION_BASE or not path.is_relative_to(ISOLATION_BASE):
        raise ValueError("Runtime, SDK, code and AVD must be below the isolation base")
    # Checking existing ancestors also catches a symlink whose final target has
    # not been created. This is a generation-time check, not a TOCTOU defense.
    if path.resolve(strict=False) != path:
        raise ValueError("Isolation paths must be canonical and symlink-free")
    return path


def _port(value: int, *, allow_privileged: bool = False) -> int:
    minimum = 1 if allow_privileged else 1024
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= 65535:
        raise ValueError(f"A concrete port in {minimum}..65535 is required")
    return value


@dataclass(frozen=True)
class SandboxConfig:
    proxy_tcp_port: int
    runtime_root: Path = ISOLATION_BASE / "guest"
    sdk_root: Path = ISOLATION_BASE / "sdk"
    avd_root: Path = ISOLATION_BASE / "guest/avd"
    immutable_code_roots: tuple[Path, ...] = ()
    homebrew_read_roots: tuple[Path, ...] = ()
    listen_tcp_ports: tuple[int, ...] = ()
    dns_loopback_port: int | None = None


def _validated(config: SandboxConfig) -> tuple[Path, Path, Path, tuple[Path, ...]]:
    _port(config.proxy_tcp_port)
    runtime, sdk, avd = map(_isolated_path, (config.runtime_root, config.sdk_root,
                                            config.avd_root))
    code = tuple(_isolated_path(path) for path in config.immutable_code_roots)
    dependencies = []
    for value in config.homebrew_read_roots:
        path = _path(value)
        # opt/ symlinks, all of Homebrew, package-only prefixes and arbitrary
        # host directories are not accepted. Pass resolved package/version paths.
        if (not path.is_relative_to(HOMEBREW_CELLAR)
                or len(path.relative_to(HOMEBREW_CELLAR).parts) < 2
                or path.resolve(strict=False) != path):
            raise ValueError("Homebrew dependencies require a canonical Cellar package/version path")
        dependencies.append(path)
    for writable in (runtime, avd):
        for immutable in (sdk, *code, *dependencies):
            if writable.is_relative_to(immutable) or immutable.is_relative_to(writable):
                raise ValueError("Writable state must not overlap SDK or immutable code/dependencies")
    if config.dns_loopback_port is not None:
        _port(config.dns_loopback_port, allow_privileged=True)
    for port in config.listen_tcp_ports:
        _port(port)
        if port in (config.proxy_tcp_port, 5037):
            raise ValueError("Do not let the emulator bind the guarded proxy or shared ADB server")
    if len(set(config.listen_tcp_ports)) != len(config.listen_tcp_ports):
        raise ValueError("Management listener ports must be unique")
    return runtime, sdk, avd, tuple(dict.fromkeys((*code, *dependencies)))


def _filter(kind: str, value: str | Path) -> str:
    return f"({kind} {sbpl_string(str(value))})"


def _deny_except(operation: str, filters: list[str]) -> str:
    if not filters:
        return f"(deny {operation})"
    return f"(deny {operation}\n    (require-not (require-any\n        " + "\n        ".join(filters) + ")))"


def generate_profile(config: SandboxConfig) -> str:
    """Return candidate SBPL text; never creates directories or changes policy."""
    runtime, sdk, avd, extra_read = _validated(config)
    writable = tuple(dict.fromkeys((runtime, avd)))
    read_roots = (*OS_READ_ROOTS, sdk, *extra_read, *writable)
    read_filters = [_filter("subpath", path) for path in read_roots]
    read_filters.extend(_filter("literal", path) for path in OS_READ_FILES)
    metadata = []
    for path in (sdk, *extra_read, *writable):
        metadata.extend(_filter("literal", parent) for parent in reversed(path.parents))
    # Metadata alone on ancestors permits traversal checks, not directory data
    # or listings. Darwin's inert executable additionally requires a literal /
    # read allowance; this permits root directory data, never its descendants.
    metadata_filters = list(dict.fromkeys((*read_filters, *metadata)))
    write_filters = [_filter("subpath", path) for path in writable]
    write_filters.append(_filter("literal", "/dev/null"))
    exec_filters = [_filter("subpath", path)
                    for path in (*OS_READ_ROOTS, sdk, *extra_read)]
    network = [f'(remote tcp "localhost:{config.proxy_tcp_port}")']
    if config.dns_loopback_port is not None:
        network.append(f'(remote udp "localhost:{config.dns_loopback_port}")')
    listeners = [f'(local tcp "localhost:{port}")' for port in config.listen_tcp_ports]
    lines = [
        "; EXPERIMENTAL ONLY: deprecated sandbox-exec, not App Sandbox acceptance.",
        "; Installer enforces UID, immutable ownership, symlinks and inherited FDs.",
        "; No arbitrary GPU/HV/mach capability bypass is added for compatibility.",
        "; Optional DNS is localhost-only to a separate guard; no public UDP53.",
        "(version 1)", "(allow default)",
        _deny_except("file-read-data file-read-xattr", read_filters),
        _deny_except("file-read-metadata", metadata_filters),
        _deny_except("file-write*", write_filters),
        _deny_except("file-map-executable", exec_filters),
        "(deny file-read* file-write* file-map-executable\n    "
        + "\n    ".join(_filter("subpath", path) for path in FORBIDDEN_ROOTS) + ")",
        # The runtime identity must not be able to rewrite immutable code or
        # another service's state, including sibling directories in the base.
        _deny_except("network-outbound", network),
        _deny_except("network-bind", listeners),
        _deny_except("network-inbound", listeners),
        "(deny appleevent-send)",
        '(deny mach-lookup\n    (global-name "com.apple.SecurityServer")\n'
        '    (global-name-regex #"^com\\.apple\\.(securityd|tccd|cfprefsd)(\\.|$)"))',
        "; Other default-allowed Mach/IOKit/process capabilities remain unaccepted.",
    ]
    return "\n".join(lines) + "\n"


def check_compiles(profile: str) -> dict:
    """Compile/apply only to an inert child; never launches or changes a VM."""
    if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
        return {"available": False, "compiled": False,
                "scope": "Harmless child only; no emulator acceptance"}
    proc = subprocess.run(["/usr/bin/sandbox-exec", "-p", profile, "/usr/bin/true"],
                          capture_output=True, text=True, timeout=10, cwd="/")
    return {"available": True, "compiled": proc.returncode == 0,
            "exit_code": proc.returncode, "stderr": proc.stderr[:2048],
            "scope": "Harmless child only; no emulator/GPU/Hypervisor acceptance"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy-port", type=int, required=True)
    parser.add_argument("--runtime-root", type=Path, default=ISOLATION_BASE / "guest")
    parser.add_argument("--sdk-root", type=Path, default=ISOLATION_BASE / "sdk")
    parser.add_argument("--avd-root", type=Path, default=ISOLATION_BASE / "guest/avd")
    parser.add_argument("--read-code", type=Path, action="append", default=[])
    parser.add_argument("--read-homebrew", type=Path, action="append", default=[])
    parser.add_argument("--listen-port", type=int, action="append", default=[])
    parser.add_argument("--dns-loopback-port", type=int)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        profile = generate_profile(SandboxConfig(
            proxy_tcp_port=args.proxy_port, runtime_root=args.runtime_root,
            sdk_root=args.sdk_root, avd_root=args.avd_root,
            immutable_code_roots=tuple(args.read_code),
            homebrew_read_roots=tuple(args.read_homebrew),
            listen_tcp_ports=tuple(args.listen_port),
            dns_loopback_port=args.dns_loopback_port))
    except ValueError as error:
        parser.error(str(error))
    if args.check:
        result = check_compiles(profile)
        print(json.dumps(result, indent=2))
        raise SystemExit(0 if result["compiled"] else 1)
    print(profile, end="")


if __name__ == "__main__":
    main()
