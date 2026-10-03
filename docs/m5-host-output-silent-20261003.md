# M5 local speaker silence

The user requested that M5 remain silent when a phone disconnects while a
guest video continues playing. The current Mac output was read over the existing
host-key-verified, key-authenticated LAN SSH connection: output volume100 and
output muted=false. Guest `volume_music` was5.

The host output is now volume0 and muted=true. Readback confirmed both values;
guest `volume_music` remains5. No guest setting was written, no emulator or
gateway was restarted, and no NPS, NPC or cloud rule was changed. The first
activity query used public15558 on the Mac, which is not its local formal
gateway port; it was corrected with a separate actual local15556 established
socket check and owner UDP45965 check, both clear. This silence operation does
not require terminating or reserving a media session.

The one-shot login LaunchAgent source is
[m5-host-output-silent.plist](../scripts/deployment/m5-host-output-silent.plist).
Its deployed location is the existing M5 user’s
`~/Library/LaunchAgents/local.huoguo.m5.host-output-silent.plist`.
SHA256 is
`9a7d2ff6a888869fc1577ad8bd6ab036a3bf2f0890a4623819296ab5baea6053`.
`plutil` accepted it; `launchctl bootstrap` returned0 and the actual job's
last exit code was0. RunAtLoad=true, no KeepAlive and no periodic polling. No
prior file or job with this label was present. A changed prior file would have
been backed up before installation, but no backup was needed this time.

This intentionally silences **all Mac local output**, including other Mac
applications. It does not lower the guest media volume. The current project
captures audio inside Android: AudioRecord/REMOTE_SUBMIX to guest AAC encoding
and the ADB audio socket, then to the phone. `hardware_stream.py`589–605 starts
and forwards that guest service; it does not recapture the Mac speaker output.
The guest's default `audio_source=output` stops local playback while capture is
active, and releases it when the capture ends. Returning to ordinary guest
speaker playback explains why a loud, unmuted host becomes audible after exit.
Changing AAC to RAW or setting `audio_dup=false` does not provide permanent
silence between sessions. See the upstream
[scrcpy audio behavior](https://github.com/Genymobile/scrcpy/blob/master/doc/audio.md)
and the current local server source.

Verification covers actual host settings, successful one-shot deployment and
unchanged guest media setting, plus the source-level separation of the capture
path. This iteration did not perform a new phone/acoustic listening test or
reboot the Mac merely to test a login action. The login job reapplies silence
when loaded or after the user next logs in; it does not continuously override
a user who later manually unmutes the Mac or changes output-device settings.
No phone APK update is needed for this host setting.

To intentionally return M5 to local playback, unload and remove this **one**
LaunchAgent, then restore the desired Mac output volume/unmute. Do not restart
the emulator, NPS, NPC or either gateway for that rollback. If the owner later
wants other Mac applications audible while only the emulator stays quiet, that
requires a separate per-process audio-output policy; it was not added here.
