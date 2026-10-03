# Source decoder observation collector contract

`scripts/probes/source_decoder_observation.py` is an independent read-only
collector. It reuses the existing PlaybackState parser, brackets observations
with exact Morphe process identity, and reports service-output availability.
It does not parse a speculative decoder dump schema. All emitted leaf values
are numbers, booleans or null; fixed JSON field names carry the meaning.

This is an implementation/fixture contract. The root's earlier independent
installed-schema observation is not a run of this final collector: that separate
read found a stable Morphe process, playing state with unchanged reported
position, a metrics output with no matched target package, and an empty successful
codec output. No old source itag is filled into the new result. No device commands
were run while implementing or verifying this collector.

## Invocation and operation scope

From the canonical repository root:

```sh
python3 scripts/probes/source_decoder_observation.py --serial emulator-5556 --timeout 15
```

The external ADB default is `~/Library/Android/sdk/platform-tools/adb`; override
it with `--adb /absolute/path/to/adb`. Timeout must be finite and within3–15
seconds. The selector is a bounded allowlist token and is supplied as an argv
element. The fixed package is `app.morphe.android.youtube`.

The collector launches these reads sequentially, once each:

1. Exact-package `pidof`, then that one PID's cmdline, status and stat.
2. `dumpsys -t 3 media_session`.
3. `dumpsys -t 3 media.metrics --prefix codec --since -15`.
4. `dumpsys -t 3 media.codec`.
5. A second `media_session` dump.
6. A second exact process identity read.

No input, install, authentication, player control, setting/property change,
service startup, `--clear`, root command or remote-process signal is issued.
The only signals are to this collector's own local ADB child after a timeout or
read failure. The children use no stdin and discard stderr.

## Bounds and cleanup

All reads share one host-monotonic deadline of at most15 seconds. The last2
seconds are reserved for cleanup; each read also has a maximum3-second read/wait
window. Reaping is limited by the remaining overall deadline and at most2
seconds. Once the shared read deadline, memory budget or unreaped-child condition
is reached, later slots return fixed skip reasons without launching another
process. The six command slots are not six independent15-second timeouts.

Each dump is capped at1MiB; identity reads are capped at8KiB. All six reads
count toward a shared4MiB raw-byte budget. Reaching a cap returns a conservative
bound failure, even if the output happened to have exactly that size; no extra
overflow-detection byte is read. Raw bytearrays remain only in memory, are
cleared after each parse and are never saved, printed or placed in errors.
Decoder-service UTF-8 validation and LF line counts are availability observations,
not schema validation. No title, URL, package inventory, arbitrary property,
stderr text or private marker can enter the numeric output.

These bounds govern the collector's waits and allocations, not a hard real-time
guarantee against an OS syscall stall. If the owned local child cannot be reaped
within the remaining deadline, `child_reaped=false` and
`all_children_reaped=false` expose that condition; later launches are disabled.
Do not treat that result as confirmed cleanup or complete evidence.

| Command error code | Meaning |
| ---: | --- |
| 0 | Read completed successfully; semantic evidence may still be unknown |
| 1 | Executable/process launch failed |
| 2 | Per-command/shared read time exhausted or child wait timed out |
| 3 | Per-read or remaining aggregate raw-byte cap reached |
| 4 | ADB read command exited nonzero |
| 5 | Local pipe/selector/close failure |
| 6 | Shared raw-byte budget already exhausted; no child launched |
| 7 | Read deadline reached or previous child remains unreaped; no child launched |

`command_ok` additionally requires that the child was reaped. A successful
empty `media.codec` dump has `command_ok=true`, `raw_available=false`,
`line_count=0`; it is not a live-format result.

## Identity and player-state association

Identity requires one positive PID, cmdline exactly equal to the fixed package
followed by one or more NUL bytes, matching Pid/Tgid, one four-column Uid row with equal
UID values, and a valid nonzero stat field22 start tick. Parentheses/spaces in
stat comm are handled relative to its final closing parenthesis. Missing,
oversized, duplicated or mismatching fields return `known=false`.

After the RAM maintenance cold boot, root's initial run of this collector
completed successfully but returned both identities unknown. A separate bounded
in-memory schema read then observed a99-byte cmdline that was exactly the target
package after stripping only trailing NULs; PID/stat and numeric status fields
were present. The parser now admits this bounded zero-padding shape while still
rejecting a missing terminator, non-NUL padding, another argument, a leading NUL,
similar package or process suffix. The new fixture retains only that public
shape/length; its process numbers and status text are synthetic. This source fix
does not reinterpret the initial collector result or count as a subsequent
successful real-device identity observation.

`identity_stable` requires known equal PID, UID and start tick before and after.
Session association separately requires one active fixed-package owner block
whose PID/UID matches that identity. The existing parser still decides whether
PlaybackState is known. Duplicate active fields, ambiguous owner blocks and
foreign/nested metadata cannot supply an association.

`playing_state_bracket` means both associated snapshots report state3 and
speed1 within a stable identity bracket. It does not prove decoded frames,
motion or display progress. `reported_position_changed=false` can coexist with
a playing-state bracket: MediaSession may keep the prior position/update while
playing. Position projection is not implemented; guest elapsedRealtime values
are not subtracted from host monotonic timestamps.

## Deliberately unknown format

Both service observations report `installed_schema_validated=false`,
`codec_identity_known=false`, and `live_format_known=false`. The metrics record
queue is marked `historical_queue_only=true`; the codec slot is not described as
a historical queue, but it still has no validated installed schema or live link.
There is no token search, latest-record guess, decoder codec-name heuristic or
quality-menu fallback.

Top-level width, height, MIME enum, configured FPS, content FPS, itag and source
bitrate remain null with their known flags false. These are separate from the
guest's physical mode, quality-menu selection, host stream dimensions/bitrate,
receive FPS and SurfaceFlinger cadence. Future installed-schema support needs
an independent scoped change and fixtures from sanitized real grammar, plus a
verified current-client association. This collector alone cannot satisfy the
strict frozen supervisor's same-format source gate or certify an A/B cohort.

## Offline validation

The new fixture suite covers exact process fields and stat start-time handling,
foreign/ambiguous session owners, unchanged playing positions, process restart,
paused state, unknown decoder output, finite input bounds, raw clearing and
numeric-only output. Local temporary fake executables test stdout caps, aggregate
caps, nonzero exits, discarded stderr, timeout/reaping and disabled later
launches. Those are owned local subprocess fixtures, not Android-device reads.

```sh
python3 -m unittest tests.test_source_decoder_observation tests.test_source_playback_state tests.test_source_player_quality
```

Actual device validation and sampling overhead remain pending. See
[the upstream review and source-format limits](source-decoder-numeric-readback-design-20261003.md).

## Independent installed-image acceptance

Root independently reran all30 offline tests and then executed the exact final
8a76822a collector on M1 after the user-requested RAM8 cold boot. The bounded
read completed in299.535ms with8,126 aggregate raw bytes, no retained raw dumps
and all owned ADB children reaped. Exact Morphe PID3553/UID10235/start2521 was
known and stable across the bracket. No active Morphe MediaSession was reported;
playing state remained unknown. `media.codec` returned exit0 and zero stdout
bytes; the1852-byte metrics queue is not a live-format witness. All format,
itag, renditionFPS and bitrate fields remained unknown. This does not satisfy
a playing/source-format gate and contains no phone media or FPS result.

The first installed-image run on the previous collector SHA successfully read
1442-byte identity records but conservatively rejected a99-byte cmdline padded
only with NULs. A separate white-list structure read confirmed that exact
shape. The final parser/fixture correction allows only the fixed target plus
at least one NUL and pure NUL padding. The initial `known=false` result remains
recorded and is not relabeled. See
[source observation receipt](source-decoder-observation-live-20261003.json).
