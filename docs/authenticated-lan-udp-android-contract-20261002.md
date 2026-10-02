# Isolated Android authenticated LAN UDP candidate

This is an opt-in experiment App, not the published v1.30 media implementation. Formal package, routes, signing configuration, saved settings and updater behavior remain selected when the opt-in property is absent. Do not publish the candidate APK as a formal update.

## Build separation

Build with both `-PprobeApplicationId=local.remoteandroid.direct.experiment` and `-PauthenticatedLanUdp=true`. The second property alone fails configuration. Only this mode includes `app/src/udp/java`, the reusable UDP receiver helpers and arm64 JNI. The native build invokes the existing pinned-dependency build tool; external defaults are `~/.cache/huoguo-v2-sources/moonlight-common-c`, `~/Library/Android/sdk/ndk/29.0.14206865` and the existing CMake tool path. No JNI or UDP implementation source set is part of the ordinary App build.

The candidate skips automatic/manual updater UI and uses its own explicit LAN login screen. The experimental package has separate Android data from the formal package. This does not erase the experiment package's existing data. The source version remains 1.30/code31; the label “认证 LAN UDP · 隔离候选” distinguishes the UI and is not a release claim.

## Authentication and descriptor

The UI accepts a literal RFC1918 IPv4 address and HTTPS port 15560. The existing M1/M5 pinned certificates authenticate the TLS peer; HTTP carries Basic authentication, requested max size, VBR target bitrate, FPS cap, buffer and component options. There are no TCP media `CONNECT` requests in this path.

`POST /udp/session` must return:

- `protocol: HGUE_UDP_V1`, a 32 lower-case-hex `session`, 32-byte Base64 `key_b64`, and 16-hex-digit `session_tag_hex`.
- `peer_host` exactly equal to the HTTPS login literal LAN IP, `peer_port: 15963`, `bind_port: 0` (random phone UDP port).
- Integer `seconds` from 1 through 120; FPS 60 or 120; buffer from 30 through 100 ms; `video_release: scheduled`.
- `display_hz: 120`, `surface_submit_lead_ms: 0`, actual Boolean `audio_enabled`, and `touch_enabled`, `async_video`, `decoder_reanchor_enabled` all true.
- Optional probe `profile` and Boolean `network_feedback`; the latter may be true.

Numeric strings, fractional/nonfinite numbers, public/Tailnet/DNS peers, mismatched HTTPS/UDP peers, old ports, immediate release and absent required components fail closed. Unsupported 120 Hz display mode is an explicit setup failure, not a silent fallback. This first candidate is specifically for the authorized 120 Hz test phone, not a V50 recommendation.

The session key and authentication header remain memory-only. No temporary session credential file, ADB command, root operation or private key is required by App authentication. The instrumentation mode retains its existing restrictive temporary-file contract for historical reproducibility.

## Media, control and lifecycle

The reused receiver handles authenticated AES-GCM video/FEC, optional AAC and native `MotionEvent` snapshots. Directional nonce sequencing is serialized across READY, ALIVE, keyframe feedback, touch and STOP. ALIVE is sent every 750 ms; receive socket timeouts are 20 ms. UDP media failure does not call formal TLS media methods.

Cancel before authentication completion closes the pending HTTPS socket. If a valid session ID is received before cancellation, the cleanup path issues `DELETE /udp/session/<id>`. A server-created session whose response is interrupted has to expire under the host's short READY lease; the client cannot delete an unknown ID.

App back/leave and background transitions cancel the generation. Receiver cleanup closes touch with authenticated CANCEL, then sends three authenticated STOP datagrams before closing its socket. HTTPS DELETE is idempotent and independently attempted; packet loss is bounded by the host lease. Reconnect cannot acquire shared Activity codec/audio resources while the prior receiver is still retiring. Late authentication/completion callbacks cannot replace a newer generation's UI.

Every reconnect creates a new touch sender under a fresh server session key/tag. Touch tokens are monotonically increasing for each session. Explicit cancel, geometry change and implicit abandoned gestures clear pending old edge retries; canceled tokens cannot be resurrected by delayed retries. Host and guest cancellation acceptance remain separate tests.

## Evidence limits

`udp-app-last-report.json` is a single overwritten private App file, at most 64 KiB. It contains selected numeric counters and numeric-only aggregate trees for FEC/audio/touch/queue; it omits host, account, session ID, key, credentials, video titles, coordinates and arbitrary exception text. It does not measure physical display/touch/acoustic latency.

Latest source audio timing diagnostics are included by this opt-in build. Their sampling overhead still needs separate evaluation; do not compare this candidate against frozen old-APK reports as if instrumentation were identical. Offline build/lint/contract checks are not real phone, public UDP, P2P, V50 or audio/video synchronization acceptance.
