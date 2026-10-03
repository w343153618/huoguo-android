# Current source format with a fresh process and session bracket

This iteration completes source-only qualification. It does not start a phone or media session and does not claim that the earlier frame stalls have been fixed. The source remains paused at6:17 in the existing M1 owner environment. The evidence is [the typed observation record](source-stats-fresh-collector-20261004.json).

## What is now verified

`source_stats_observation.py` calls the existing bounded ADB reader for exact process cmdline/PID/UID/starttime, current focus and active MediaSession before and after one Stats UI snapshot. It requires the selected paused/playing state throughout the bracket. A visible contrary player control also rejects qualification; disappearing controls during playback do not replace the independent MediaSession check. Only the native player’s actual Video ID and split format fields qualify the descriptor.

The successful source-only runs retain PID3470/UID10235/start1952 and the original guest instance48576. They read actual `aqz-KE-bpKQ`, videoitag299/avc1/1920×1080@60, audioitag251/Opus, viewport1080×608, cumulative drops0/11319 and position377seconds. The60 is the format descriptor, not measured decoding or presentation. The pure UI parser retains `process_identity_verified=false`; the new enclosing observation separately verifies the process/session/focus bracket.

The collector caps the whole operation at15seconds, each local ADB query at3seconds, one output at1MiB and total output at4MiB. XML remains in memory and is cleared after parsing. One internal random24hex suffix selects an atomically created0700 device directory; the UI tool writes only `ui.xml` there. Success requires the command to remove that file and directory before emitting its completion receipt. If a failing command has proved directory creation, the collector attempts removal of only that owned path. Failed local ADB reaping, remote command completion and temporary-file removal stay separate. A timeout cannot be described as a reaped remote UI process.

Default CLI execution is inert. Explicit collection opens no menu, sends no input, resumes no video, and does not clear data, metrics or settings. The diagnostic overlay was already on and stays on. Collection/overlay cost under active media has not been measured.

## First live attempt and correction

The first candidate returned exit2 in4.339seconds and started no media. Full process identity and paused MediaSession matched. Its `window windows` subcommand omitted the required current-focus row, and directing the UI dump to `/proc/self/fd/1` produced65bytes of status text without XML despite command exit0. Neither result was accepted as a valid format snapshot.

The final collector uses full `dumpsys window` and the private owned temporary described above. The corrected collector run finished in4.427seconds; its collection took2.314227334seconds and168334bytes. The additional native-clock gate run finished in4.483seconds; collection took2.339393625seconds and168593bytes. Its UI command returned60018bytes including the two fixed receipts, with successful temporary removal. All seven collector children were reaped. Each surrounding protection runner separately started/reaped62local clients with no timeout termination; those62do not include the collector’s seven children.

Both successful runs held the continuous45965reservation, obtained the exact original registry503/`udp_worker_unavailable` witness, checked original identity and all four roles plus formal TCP inactivity, and verified the original guest job/boot/configuration before explicitly releasing the reservation. Original M1 gateway, M5, NPS, phone, resource settings and user data were not controlled or restarted. The fresh run verifies guest renderer properties; its report leaves actual source pipeline for this round null rather than substituting a previous Vulkan readback.

## Gate and sampling-window boundary

`owner_source_stats_gate.py` is independent of the existing format-unknown `owner-source-gate-v1`. It calls the trusted in-process collector rather than loading externally supplied observations. It requires the expected process identity, complete collection, expected Video ID and closed format values. Playing qualification also requires a listener-ready timestamp preceding collection.

The wrapper brackets the call using host `CLOCK_MONOTONIC`; the reader’s Python monotonic stamps remain a distinct named domain. Each domain has its own15second elapsed check. Freshness uses only native-domain differences and expires after30seconds. No native/Python, guest position or phone clock values are subtracted across domains.

Before/after descriptors may establish matching endpoints around a sample window. They cannot prove that content never changed in the middle, visual motion, actual decode/presentation FPS or touch latency. The gate explicitly leaves those claims false/null. Window integration must resume only under protected admission, collect a fresh playing descriptor after the listener is ready, and pause/read back adjacent to the sampler’s end. Long helper preparation and300second natural gateway shutdown must not silently let autoplay replace the source.

The actual gate call used SHA938b0b22… . The final source adds only pure freshness/endpoint validation; `collect_fresh` is unchanged. The old exact pinned gate blob is retained privately, and its recorded result passes the final pure validator offline. That is not a live execution of the final file or a fresh observation now. The detailed JSON separates actual and final SHAs.

## Validation and next step

Root’s104focused checks passed in2.113seconds, including the unchanged format-unknown gate tests. The new tests cover default inactivity, foreign/ambiguous focus, process restart and owner mismatch, contrary playback state, success text without qualification, bounded pipe receipts, owned cleanup failure, nonce injection, descriptor leakage, listener/freshness checks and endpoint overclaims. These are source/owned fixtures, not phone or performance results.

The previous parser commit41b0a7d…/GitHub run37150866427 was read back with overall, `build` and `udp_candidate` success. It does not validate the new collector commit.

Next is one NEW frozen, protected M1/OnePlus12 LAN window with alpha8,540×960/30cap/4MVBR/80ms/lead0, stage/startup/PCMqueueOFF, AACON, FIFO2/native3. Bind actual source format and position to that window, record the overlay, and separate initialization from steady timing. Do not replay the completed source menus or expand queues/buffering to mask missing supply. This step is not yet executed.
