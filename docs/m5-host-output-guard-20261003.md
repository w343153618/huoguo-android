# Event-driven M5 host output guard

Root has now deployed and validated this guard on the actual M5. The
implementation agent only built and reviewed it; deployment and the live
event check were performed separately by root. The previous one-shot
`m5-host-output-silent.plist` remains unchanged and backed up. The user explicitly wants M5
local playback to remain silent after disconnect; manual changes to a protected
Mac output's volume/mute will therefore be corrected while this guard runs.

`scripts/deployment/m5_host_output_guard.swift` uses the installed Apple CoreAudio
SDK's `AudioObjectGet/SetPropertyData` and property-listener APIs. It does not run
AppleScript, poll, record audio, use a tap, alter input audio or contact Android.
The existing remote audio path is Android `REMOTE_SUBMIX` → AAC → ADB → phone,
not a recording of the Mac speakers. Source separation does not replace a new
phone listening test after deployment.

Root's fresh M5 read-only audit identified `BuiltInSpeakerDevice` (163) and
`BlackHole2ch_UID` (113); IDs are transient, so the guard resolves explicit UIDs
on each event. It protects recognized physical default-output and default-system
output devices plus the exact pinned speaker, rechecking that it is built-in.
It sets output master mute/volume when supported, and also checks output channels;
silence is confirmed only with a silent master or all observed output channels.
There is no input/default-input write. Virtual/Oray outputs are untouched;
aggregate or unknown defaults are reported as unclassified, never healthy silence.

If a current physical default cannot confirm silence (including a writable
control whose read/set fails), the only fallback is the explicitly pinned
BlackHole output UID. Only the affected default selector is changed. A pinned
speaker that is no longer default cannot trigger an unrelated route change.
The guard does not control applications explicitly using some other device and
always reports `all_output_routes_quiet_verified=false`.

Build and inert checks (no device access):

```sh
xcrun swiftc -O -framework CoreAudio scripts/deployment/m5_host_output_guard.swift -o /private/tmp/m5-host-output-guard
/private/tmp/m5-host-output-guard --self-test
plutil -lint scripts/deployment/m5-host-output-guard.plist
```

Root-only live modes after reviewing the source:

```sh
m5-host-output-guard --audit
m5-host-output-guard --once --speaker-uid BuiltInSpeakerDevice --sink-uid BlackHole2ch_UID
m5-host-output-guard --watch --speaker-uid BuiltInSpeakerDevice --sink-uid BlackHole2ch_UID
```

`--audit` reads output UIDs, defaults and each master/channel control's readable
and writable state; it does not write. `--once` writes/reads once and returns3 if
the selected policy is uncertain. `--watch` installs CoreAudio listeners on
device/default changes and output properties, coalesces work on a serial queue,
and logs only changed policy state or an actual correction. Unknown/partial
control state is reported rather than assumed silent. Listener failure is also
reported; the guard does not claim continuous protection when registration fails.

The new plist is a template with exact UID/runtime placeholders and a separate
job label. It starts a persistent event-driven process; unsuccessful termination
can restart with a15-second throttle. It never restarts an emulator or gateway.
Root must back up and substitute the template, verify identities/signatures and
choose the handoff from the old one-shot job. Rollback removes only this new job;
then restore the desired host output settings. The implementation agent performs
no launchctl, host-volume, guest-volume or device test.

## Actual M5 deployment and safe event check

A fresh read showed output50/unmuted despite the earlier one-shot mute. Root
restored output0/mutedtrue immediately, then deployed the independently reviewed
CoreAudio guard under `local.huoguo.m5.host-output-guard`. Actual bootstrap was0,
PID5917 at acceptance; sourceSHA7e9efb8d, binarySHA92e43b15 and the substituted
plistSHA b0bcc61e matched. Both current default and system output were the pinned
built-in speaker. The actual master mute and volume controls were readable and
writable. BlackHole's exact output UID was independently audited, but the
fallback was not used (`sink_routes=0`).

The live event test kept volume0 throughout and only toggled muteoff. The guard
performed one real correction and actual output was again0/mutedtrue. Listener
errors, uncertain devices, failed writable controls and stderr bytes were0.
Guest `volume_music` remained5; input volume remained85. No audible test tone,
volume1 experiment, emulator/gateway/NPS restart or new phone installation was
performed. The first local receipt-writing wrapper omitted its Path import
after the remote assertions had already succeeded. A read-only follow-up saved
the actual event log and state; the event was not repeated to repair that local
writer. See [live receipt](m5-host-output-guard-live-20261003.json).

Installed files are in the restricted M5 path
`~/Library/Application Support/AndroidRemote/host-output-guard-20261003`;
its backup retains the old one-shot plist, before-volume settings and initial
read-only device audit. The new plist is
`~/Library/LaunchAgents/local.huoguo.m5.host-output-guard.plist`. Root preserved
the old one-shot job, existing guest settings, accounts, data and remote services.

This acceptance covers the actual built-in/default physical speaker and one
safe unmute event. Device hotplug, aggregate routes, fallback switching, login
after reboot and fresh phone acoustic listening were not tested. Do not repeat
this completed maintenance in a heartbeat, raise the volume to test sound, or
restart the VM to validate it. The guard intentionally corrects manual local
volume/mute changes while active. To disable it, unload only this new job; retain
its backup and the previous one-shot setting.
