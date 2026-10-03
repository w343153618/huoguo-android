# Alpha6 public owner trial and stable1.31 delivery — 2026-10-03

Stable1.31/code32 and experimental1.31-alpha.6/code37 were published as separate
signed packages and immutable GitHub releases. Both original signing identities
were verified. The stable artifact's entire body was read through public M1 and
M5 HTTPS15556/15558; the experimental artifact was read through the pinned M1
mirror. SHA, size, metadata and source tags matched. APKs and raw reports remain
outside Git. Stable media stays TLS/TCP; the experimental App uses authenticated
UDP media on both public node profiles and never silently falls back to TCP.

Source identities: stable86189ce5d7469cd9bc474cc0006158c74eedc537;
alpha6b38587d241eaa51e713e469968603fa0febc86ab. The owner gateways used the
compatible frozen host source924879df231d708224b5a3e70ae941681995c35b. Alpha6 APK
SHA10080a883ae563850603a9e375f03a5b860596766eeed25accbda6a08bae29e5;
JNI981f13d279ffc20232816c1e646256189cf35e02181353e9eaba89041832cd03.

## Real public-address phone round

The rooted, CPU-limited OnePlus12 on the same home Wi-Fi authenticated with the
existing owner account through HTTPS49556/49558, received audio/video through
public UDP15556/15558, left normally and authenticated a second session for each
node. Parameters were 4Mbps VBR, 60FPS cap, 80ms buffer, lead0, stage diagnostics
on, startup-ready gate off, AAC on and the independent PCM queue off. Tests were
media-only: no new multi-touch or physical latency acceptance is implied.

| Node | App source size | Independent phone SF cadence | Full20s window | Max gap | >100ms |
| --- | --- | --- | --- | --- | --- |
| M1 | 1080×1920 | 58.153FPS | 56.749FPS | 298.344ms | 2 |
| M5 | 720×1280 | 24.045FPS | 23.498FPS | 66.311ms | 0 |

M1 used real blue-M YouTube playback; its exact selected format/position was not
independently read in this round. M5 began with Big Buck Bunny, then autoplay
changed to **SNOW BEAR - A Hand-Drawn Animated Short Film (4K) by Aaron Blaise**.
The generic M5 test wrapper still labels BBB; that label is not evidence of a
fixed60FPS source. The M5 result must not be presented as a controlled60FPS
encoding comparison or as a VM60FPS ceiling. Source SurfaceFlinger was not
sampled by this phone-only campaign. Decoder callbacks echo requested targets
and are not independent presentation timestamps; SF evidence is presentation
cadence, not unique content frames or touch-to-photon delay.

An earlier M5 attempt was canceled by the conservative formal-session guard;
reconnect was rejected while a formal15556 socket existed. That socket might be
a formal session or an update download, so its existence alone cannot identify
the user. The failed attempt is retained separately. Final worker records show
natural native exits0 and complete cleanup, without TERM/KILL.

During the successful M5 round, a bounded header-only cloud capture observed
835UDP15558 and1165UDP8025 packets, all from the home's domestic physical source,
with zero kernel drops. This associates the public front door and NPC QUIC outer
bridge with the test window; it does not establish per-packet end-to-end
causality, QUIC Datagram internals, P2P, remote northeast Wi-Fi or cellular.

The phone's Tailscale VPN initially blocked public reachability. Stopping its
App, without logging out or deleting its node, restored the public ping and
allowed the tests above. Consequently these results are **VPN off**; a candidate
per-socket physical Network binding still needs real VPN-on validation. The
registered M1/M5/phone nodes remain unchanged.

## Update UI and limits

On the same phone, stable1.31 opened the two-channel picker, displayed alpha6's
actual changelog and opened the already-installed test package. Alpha6 likewise
displayed stable1.31 details and opened the stable package. The selected package's
own installed version was used, so beta37 was not mistaken for stable32. The
signed stable package was installed while preserving data; absent-package App
download/installer permission resumption was not exercised by this UI check.

The owner trial is nonisolated, single-session bounded to120 seconds. The user
reported a fluent OnePlus15 experience, but that subjective feedback does not
identify an independently measured cellular path or complete friend deployment
safety. Friend/public host and LAN isolation, V50, physical multi-touch, optical
delay and acoustic lip sync remain separate gates. Prepared durable LaunchAgents
have not yet been loaded; see owner-udp-launchagents-20261003.md.

Evidence index: docs/evidence/nps-public-udp-contract-20261003/, including both
phone-node reports, public full-APK delivery receipts, GitHub readbacks and the
failed guarded M5 attempt. No credentials, APKs or raw logs are committed.
