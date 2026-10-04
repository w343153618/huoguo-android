# M1 helper scope FD retirement candidate — 2026-10-04

Default OFF. The standalone C candidate now passes 23 actual host scope/FD checks
within 61 affected checks (6.591s). The earlier 22-check/1.790s and
60-affected/5.304s passes are preserved as preceding subsets. A separate arm64/API30 build using the
existing locked NDK29.0.14206865 completed. Its ELF architecture and absence of
host fixture hooks were checked. It was not staged or executed on Android. No
App, helper APK, JNI, release manifest, phone data or service changed.

The important boundary is the containing directory. `unlinkat(dirfd, name)`
still resolves a name; it is not an atomic compare-and-unlink operation. The old
flat shell-owned `/data/local/tmp/huoguo-native-helper-*` scope cannot safely be
retired using an earlier inode snapshot alone. Existing old scope probes, private
receipt binding and freezes are preserved and remain incompatible with this new
retirement candidate. No fallback removes those directories.

The NEW layout requires the existing `/data/local` base to be independently
captured as UID0 and non-writable to other UIDs, with no special mode bits. The
candidate never modifies its permissions. A fresh protected parent is
`/data/local/huoguo-helper-root-<24-lowercase-hex>` with root:shell/0710; its
`stage` child is initially shell:shell/0700 and contains only `owned.apk` at0600.
Shell can traverse the known parent and push into the child without permission
to rename the parent or stage. This is a source contract; actual Android DAC,
SELinux, path traversal, staging and root PM behavior have not been accepted.

The finalizer opens base, parent, child and regular APK through no-follow FDs,
requires all captured dev/inode values and closed entries, and verifies the
exact matching single-window helper SHA/86419bytes. It seals the child and APK
to root, then checks size, single link, mode, inode, contents and named nodes
again. It unlinks only that APK, removes the empty child and protected parent,
and closes its FDs. The base and unrelated entries remain. Foreign nodes,
extra files, hardlinks, symlinks or unknown metadata cause refusal. A failure
after partial progress reports that progress and keeps scope status unknown;
it does not recursively clean up or silently retry against later nodes.

This relies on a trusted caller independently excluding competing privileged
writers and remaining writable FDs. `chmod` does not revoke an existing write FD;
rehashing does not create an atomic content hold. The host fixture runs under the
current host UID/GID and cannot prove Android root-versus-shell enforcement.
Injected stage/file replacement, oversized same-inode growth after sealing, and
a foreign entry after APK unlink all refuse and retain foreign or remaining
nodes. Independent hashlib vectors cover SHA padding/block/4KiB/max boundaries.
The initial basename prefix length error rejected the fixture arguments before
scope operations; it was corrected before the passing checks and Android build.

The internal 1800ms checks are cooperative; filesystem calls and local process
creation are not absolute wallclock guarantees. The existing caller 3s command
budget remains. The receipt always refuses atomic-unlink, remote PM quiescence
and reservation-release claims. A valid receipt or local ADB exit/EOF does not
prove an installer, remote worker, operator, App Attempt or server lease.
Complete NEW protected staging and owned remote lifecycle binding is still
required before this binary can run. Unknown cleanup must retain actual held
objects and the live supervisor/admission socket; it cannot release from JSON.

One phone availability query this turn returned device-not-found; no repoll,
helper install, authentication, media, source input, RPC or UI followed. When
it actually returns, the unchanged reviewed f0/d043/helper82f authenticated
frame-only qualification still comes first. The new numeric App f97b/JNI447f
and helper28db remain local only. Public alpha8/code39 and stable1.31/code32
remain unchanged. This candidate is neither Android scope acceptance nor UDP
performance evidence.

The preceding 5bbeb767/run37209710122 was independently overall/build/UDPsuccess
(2018 Linux tests/86.098s/11 existing skips). It does not cover this new source.
The new commit must receive its own CI readback, with no old-run reruns or
workflow/guard changes. Continue with NEW protected stage/readback/writer-exit
fixtures and complete supervisor binding; do not execute a partial live installer.

Exact bc4cff63391b413867bcf51ac7fbefd91144d357/run37212308330 has now been
independently read back completed/overall/build/UDPsuccess. The build log reports
2041 Linux tests/85.523s/11 existing skips. No rerun or old green was borrowed.
This cloud result is a local documentation append for the next meaningful source
push; it does not cover the private follow-on.

NEW private protected-layout argument/operation contract passes 13 pure/synthetic
checks (0.001s) and two actual host C report roundtrips: complete owned scope0,
and partial unlink with retained foreign entry2. It rejects old flat tmp paths,
foreign/mismatched metadata, duplicate/extra/unhashable/contradictory JSON, claimed
atomicity/PM/release and host fixtures presented as Android. Every metadata
selection remains stage/retirement ineligible; even completed C JSON keeps
idle-for-release false. It has no client/installer/supervisor execution. NEW
protected creation, actual remote writer/PM ownership and full binding remain
next; local ADB Success/EOF alone is insufficient. Old private bundles unchanged.
