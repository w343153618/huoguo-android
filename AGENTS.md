# Working rules for this repository

- Treat this directory as the canonical source tree for the current Android remote project. Keep changes and evidence here; the previous `android-remote/github/huoguo-android` checkout is a legacy snapshot.
- Preserve live M1/M5 emulator, gateway, NPS and download-service state. Source-tree migration does not authorize deleting the old runtime directory or changing a production service without verification.
- Never commit credentials, private keys, signing keys, `auth.json`, NPC vkeys, APKs, AVD disks or unredacted logs. Keep deployment secrets in existing restricted host locations.
- Distinguish source-level checks, synthetic probes, real M1 video, phone LAN, phone cellular and remote V50 results. Record transport path, video source, requested versus received/displayed FPS, jitter and audio/video timing before making a performance claim.
- The user finds 120 ms playback buffering too laggy; use at most 80 ms for a recommended phone setting unless the user changes that preference.
- Use relative paths in new scripts and documents where possible; document required external runtime paths explicitly. Preserve the existing Git remote and uncommitted work.
