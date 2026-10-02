#!/usr/bin/env python3
"""Prepare a private pinned staging bundle; never execute administrator actions.

The generated command is an artifact for a later explicit staging operation.
Preparing does not invoke osascript/sudo, create service accounts/root runtime
directories, stop/copy an AVD, change PF, start guards, or read credentials.
The privileged payload snapshots verified bytes into a root-private temporary
directory before provisioning, never imports code from the writable checkout.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import types


CANONICAL_SOURCE = Path("/Users/wyw/Documents/Codex/others/huoguo-android")
DEFAULT_SDK = Path("/Users/wyw/Library/Android/sdk")
ISOLATION_ROOT = Path("/private/var/lib/huoguo-android-isolation")
MAX_FILE_BYTES = 1024 * 1024
MAX_MODULES = 64
MAX_TOTAL_BYTES = 16 * 1024 * 1024
HELPERS = ("isolation_admin.py", "emulator_sandbox_profile.py")
_MODULE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}\.py\Z")


@contextmanager
def directory_fd(path: Path):
    if (not path.is_absolute() or any(piece in (".", "..") for piece in str(path).split("/"))
            or any(ord(char) < 32 or ord(char) == 127 for char in str(path))):
        raise ValueError("absolute canonical directory without control characters required")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open("/", flags)
    try:
        for piece in path.parts[1:]:
            next_fd = os.open(piece, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


def read_leaf(parent_fd: int, name: str, limit: int = MAX_FILE_BYTES) -> bytes:
    if "/" in name or name in ("", ".", ".."):
        raise ValueError("a fixed leaf filename is required")
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 0 <= before.st_size <= limit):
            raise ValueError("input must be a bounded single-link regular file")
        chunks, count = [], 0
        while count <= limit:
            block = os.read(fd, min(65536, limit + 1 - count))
            if not block:
                break
            chunks.append(block)
            count += len(block)
        after = os.fstat(fd)
        if (count > limit or count != before.st_size
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise ValueError("input changed or exceeded the size limit")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _write_leaf(parent_fd: int, name: str, data: bytes) -> None:
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=parent_fd)
    try:
        os.fchmod(fd, 0o600)
        offset = 0
        while offset < len(data):
            offset += os.write(fd, data[offset:])
        os.fsync(fd)
    finally:
        os.close(fd)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _snapshot_generator(data: bytes):
    name = "_huoguo_prepare_profile_snapshot"
    module = types.ModuleType(name)
    module.__file__ = "<verified-sandbox-generator>"
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        exec(compile(data, module.__file__, "exec", dont_inherit=True), module.__dict__)
        return module
    finally:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


def _profile(generator_bytes: bytes) -> bytes:
    module = _snapshot_generator(generator_bytes)
    config = module.SandboxConfig(
        proxy_tcp_port=18131,
        runtime_root=ISOLATION_ROOT / "homes/vm",
        sdk_root=ISOLATION_ROOT / "shared/sdk",
        avd_root=ISOLATION_ROOT / "homes/vm/.android/avd",
        immutable_code_roots=(ISOLATION_ROOT / "shared/code",),
        listen_tcp_ports=(5566, 5567, 8566),
        dns_loopback_port=53,
    )
    return module.generate_profile(config).encode("utf-8")


# This is self-contained stdlib-only source, never imported from the checkout.
# Fixed leaf names and embedded hashes are the authorization boundary, not
# paths supplied by the manifest. The outer command pins this payload itself.
_ROOT_LOADER = r'''import hashlib,json,os,stat,sys,tempfile,shutil,types
from pathlib import Path
EXPECTED = __EXPECTED__
BUNDLE = __BUNDLE__
LIMIT = 1048576

def _open_directory(path):
    if not path.startswith('/') or any(p in ('.','..') for p in path.split('/')):
        raise ValueError('noncanonical directory')
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    fd=os.open('/',flags)
    try:
        for part in Path(path).parts[1:]:
            child=os.open(part,flags,dir_fd=fd)
            os.close(fd);fd=child
        return fd
    except BaseException:
        os.close(fd);raise

def _read(parent,name,expected,limit=LIMIT):
    if '/' in name or name in ('','.','..'):raise ValueError('invalid fixed leaf')
    fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
    try:
        before=os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink!=1
            or before.st_uid!=EXPECTED['owner_uid'] or before.st_mode&0o077
            or not 0<=before.st_size<=limit):raise ValueError('unsafe bundle file')
        data=b''
        while len(data)<=limit:
            block=os.read(fd,min(65536,limit+1-len(data)))
            if not block:break
            data+=block
        after=os.fstat(fd)
        if (len(data)>limit or len(data)!=before.st_size
            or (before.st_size,before.st_mtime_ns,before.st_ctime_ns)
            !=(after.st_size,after.st_mtime_ns,after.st_ctime_ns)
            or hashlib.sha256(data).hexdigest()!=expected):raise ValueError('bundle hash changed')
        return data
    finally:os.close(fd)

def _subdirectory(parent,name):
    fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
    info=os.fstat(fd)
    if info.st_uid!=EXPECTED['owner_uid'] or info.st_mode&0o077:
        os.close(fd);raise ValueError('unsafe bundle directory')
    return fd

def _verify_bundle():
    fd=_open_directory(BUNDLE)
    try:
        info=os.fstat(fd)
        if info.st_uid!=EXPECTED['owner_uid'] or info.st_mode&0o077:
            raise ValueError('unsafe private bundle')
        manifest=_read(fd,'manifest.json',EXPECTED['manifest_sha256'],65536)
        record=json.loads(manifest)
        if record!=EXPECTED['manifest']:raise ValueError('manifest not expected')
        profile=_read(fd,'guest.sb',EXPECTED['profile_sha256'])
        result={}
        for directory,hashes in (('modules',EXPECTED['modules']),('helpers',EXPECTED['helpers'])):
            child=_subdirectory(fd,directory)
            try:
                if set(os.listdir(child))!=set(hashes):raise ValueError('unexpected snapshot files')
                result[directory]={name:_read(child,name,digest) for name,digest in hashes.items()}
            finally:os.close(child)
        result['profile']=profile
        return result
    finally:os.close(fd)

def _materialize_root_snapshot(contents):
    # Fixed trusted scratch parent, walked without following symlinks.
    parent=_open_directory('/private/var/tmp')
    try:
        info=os.fstat(parent)
        if info.st_uid!=0 or (info.st_mode&0o022 and not info.st_mode&stat.S_ISVTX):
            raise ValueError('unsafe root scratch parent')
    finally:os.close(parent)
    temporary=Path(tempfile.mkdtemp(prefix='huoguo-pinned-stage-',dir='/private/var/tmp'))
    try:
        os.chmod(temporary,0o700)
        if temporary.stat().st_uid!=0:raise ValueError('scratch must be root-owned')
        modules=temporary/'modules';modules.mkdir(mode=0o700)
        parent=_open_directory(str(modules))
        try:
            for name,data in contents['modules'].items():
                fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
                try:
                    cursor=0
                    while cursor<len(data):cursor+=os.write(fd,data[cursor:])
                    os.fsync(fd)
                finally:os.close(fd)
        finally:os.close(parent)
        return temporary,modules
    except BaseException:
        shutil.rmtree(temporary);raise

def _helper(name,data):
    logical='_huoguo_pinned_'+name.replace('.','_')
    module=types.ModuleType(logical)
    module.__file__='<pinned-'+name+'>'
    sys.modules[logical]=module
    exec(compile(data,module.__file__,'exec',dont_inherit=True),module.__dict__)
    return module

def _run():
    if os.geteuid()!=0 or sys.platform!='darwin':raise PermissionError('root Darwin staging only')
    contents=_verify_bundle()
    admin=_helper('isolation_admin.py',contents['helpers']['isolation_admin.py'])
    admin.require_root()
    if str(admin.ROOT)!='/private/var/lib/huoguo-android-isolation':
        raise ValueError('unexpected candidate root')
    temporary,modules=_materialize_root_snapshot(contents)
    try:
        state=admin.provision(modules,Path(EXPECTED['sdk']))
        destination=admin.ROOT/'profiles/guest.sb'
        try:
            admin.write_new_file(destination,contents['profile'],mode=0o644,uid=0,gid=0)
            state['guest_profile']={'path':str(destination),'sha256':EXPECTED['profile_sha256']}
            admin.update_state(state)
        except BaseException as error:
            state.update(phase='staging_failed',failure_class=type(error).__name__,
                         staging_operation='guest_profile')
            try:admin.update_state(state)
            except BaseException:raise RuntimeError('profile failure journal could not be updated') from error
            raise
        result={'schema':1,'phase':'staged','root':str(admin.ROOT),
            'profile_sha256':EXPECTED['profile_sha256'],'module_sha256':EXPECTED['modules'],
            'avd_cloned':False,'pf_changed':False,'services_started':False,
            'production_changed':False,'isolation_accepted':False}
        print(json.dumps(result,sort_keys=True))
        return result
    finally:shutil.rmtree(temporary)

if __name__=='__huoguo_root_loader__':_run()
'''


def _outer_program(payload_path: Path, payload_digest: str, owner_uid: int) -> str:
    # NOFOLLOW protects every component, not only the payload's final leaf.
    return ("import os,stat,hashlib\n"
            f"path={str(payload_path)!r}; expected={payload_digest!r}; owner={owner_uid!r}\n"
            "flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW\n"
            "parent=os.open('/',flags)\n"
            "try:\n"
            " for part in path.split('/')[1:-1]:\n"
            "  child=os.open(part,flags,dir_fd=parent);os.close(parent);parent=child\n"
            " fd=os.open(path.split('/')[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)\n"
            "finally:os.close(parent)\n"
            "try:\n"
            " info=os.fstat(fd)\n"
            " if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=owner or info.st_mode&0o077 or info.st_size>1048576:raise ValueError('unsafe pinned loader')\n"
            " data=b''\n"
            " while len(data)<=1048576:\n"
            "  block=os.read(fd,min(65536,1048577-len(data)))\n"
            "  if not block:break\n"
            "  data+=block\n"
            " if len(data)>1048576 or len(data)!=info.st_size or hashlib.sha256(data).hexdigest()!=expected:raise ValueError('pinned loader changed')\n"
            "finally:os.close(fd)\n"
            "exec(compile(data,'<pinned-root-loader>','exec',dont_inherit=True),{'__name__':'__huoguo_root_loader__','__file__':'<pinned-root-loader>'})\n")


def _applescript_literal(value: str) -> str:
    # Shell program contains physical newlines; use AppleScript quoted strings
    # with escaped newlines, quotes and backslashes, never raw interpolation.
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r") + '"'


def prepare(source: Path, sdk: Path, output: Path) -> dict:
    if os.geteuid() == 0:
        raise PermissionError("prepare-only must run without root")
    source, sdk, output = map(Path, (source, sdk, output))
    # Open first: reject symbolic path components without silently resolving.
    with directory_fd(source) as source_fd, directory_fd(sdk):
        names = sorted(name for name in os.listdir(source_fd) if name.endswith(".py"))
        if not names or len(names) > MAX_MODULES or not all(_MODULE_NAME.fullmatch(name) for name in names):
            raise ValueError("bounded ordinary root Python module names required")
        modules = {name: read_leaf(source_fd, name) for name in names}
    with directory_fd(source / "scripts/security") as helpers_fd:
        helpers = {name: read_leaf(helpers_fd, name) for name in HELPERS}
    if sum(map(len, (*modules.values(), *helpers.values()))) > MAX_TOTAL_BYTES:
        raise ValueError("snapshot exceeds total size limit")
    profile = _profile(helpers["emulator_sandbox_profile.py"])
    owner = os.getuid()
    manifest = {"schema": 1, "scope": "prepare-only; root staging not executed",
                "source": str(source), "sdk": str(sdk), "owner_uid": owner,
                "modules": {name: sha256(data) for name, data in modules.items()},
                "helpers": {name: sha256(data) for name, data in helpers.items()},
                "profile_sha256": sha256(profile), "candidate_root": str(ISOLATION_ROOT),
                "proxy_port": 18131, "dns_port": 53, "management_ports": [5566, 5567, 8566]}
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    expected = {"owner_uid": owner, "sdk": str(sdk), "manifest": manifest,
                "manifest_sha256": sha256(manifest_bytes), "modules": manifest["modules"],
                "helpers": manifest["helpers"], "profile_sha256": manifest["profile_sha256"]}
    # Output must be new; never adopt, remove or overwrite an existing bundle.
    with directory_fd(output.parent) as parent:
        os.mkdir(output.name, 0o700, dir_fd=parent)
    with directory_fd(output) as bundle_fd:
        os.fchmod(bundle_fd, 0o700)
        for directory, files in (("modules", modules), ("helpers", helpers)):
            os.mkdir(directory, 0o700, dir_fd=bundle_fd)
            child = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=bundle_fd)
            try:
                for name, data in files.items():
                    _write_leaf(child, name, data)
            finally:
                os.close(child)
        _write_leaf(bundle_fd, "guest.sb", profile)
        _write_leaf(bundle_fd, "manifest.json", manifest_bytes)
        substitutions = {"__EXPECTED__": repr(expected), "__BUNDLE__": repr(str(output))}
        payload = re.sub(r"__EXPECTED__|__BUNDLE__",
                         lambda match: substitutions[match.group()], _ROOT_LOADER).encode()
        if len(payload) > MAX_FILE_BYTES:
            raise ValueError("loader exceeded its size limit")
        _write_leaf(bundle_fd, "root-loader.py", payload)
        outer = _outer_program(output / "root-loader.py", sha256(payload), owner)
        shell_command = shlex.join(["/usr/bin/python3", "-I", "-S", "-c", outer])
        script = "do shell script " + _applescript_literal(shell_command) + " with administrator privileges"
        command = shlex.join(["/usr/bin/osascript", "-e", script]) + "\n"
        _write_leaf(bundle_fd, "command.txt", command.encode())
    return {"schema": 1, "prepared": True, "executed": False,
            "bundle": str(output), "manifest": str(output / "manifest.json"),
            "manifest_sha256": sha256(manifest_bytes), "loader_sha256": sha256(payload),
            "command_sha256": sha256(command.encode()), "module_sha256": manifest["modules"],
            "helper_sha256": manifest["helpers"], "profile_sha256": manifest["profile_sha256"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=CANONICAL_SOURCE)
    parser.add_argument("--sdk", type=Path, default=DEFAULT_SDK)
    parser.add_argument("--output", type=Path, required=True,
                        help="new private bundle below an existing ignored evidence directory")
    args = parser.parse_args()
    if args.source != CANONICAL_SOURCE:
        parser.error("CLI source must be the canonical project directory")
    evidence = CANONICAL_SOURCE / "docs/evidence"
    if args.output == evidence or not args.output.is_relative_to(evidence):
        parser.error("CLI output must be below the ignored project evidence directory")
    result = prepare(args.source, args.sdk, args.output)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
