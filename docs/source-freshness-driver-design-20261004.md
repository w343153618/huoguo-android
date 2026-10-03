# Automatic fresh-source admission for the next owner LAN trace

This is a read-only design, not an implemented or executed candidate. No source,
private frozen supervisor, phone, guest, gateway or NPS service was changed for
this review. It provides a bounded next action after the CI repair; it does not
claim a new media trace, a known video format or an improvement in FPS.

## Current evidence and the avoidable wait

The private state recorded in
`docs/evidence/host-raw-timing-20261003/iteration-state.json` identifies the last
RAM8 preparation as
`/private/tmp/huoguo-hosttrace-formatunknown-r3gjd_lr/prepare_supervisor.py`,
SHA256 `0257e1d05e839c9bfd7f008027736e5bec2114222e25af8b104dd0d56edf1c07`.
That attempt ended with `root_enter_deadline`: no driver, helper, credentials on
the phone, READY, media or trace. Actual owned quiescence and zero signals were
recorded. The previous supervisor and this one must remain historical artifacts.

The reviewed supervisor starts a frozen f08 LAN listener, then waits up to180
seconds for a human ENTER and a separately written source file. Interrupted tool
work can exhaust that window without testing the pipeline. Its current gate
also requires `visual_motion_observed=true`; the numeric collector cannot
truthfully produce that field.

Simply replacing ENTER with a collector immediately before the supervisor's
driver Popen is insufficient for a strict freshness bound. The frozen f08 driver
then performs a formal-session check and three bounded ADB preflight/cleanup
commands before its own `am instrument` Popen. The source observation could age
while those commands run. In the reviewed frozen driver, the correct insertion
point is after those checks and marker cleanup, immediately before the
instrumentation Popen around lines407–419. Any new driver must have a new pin;
the current driver SHA must not be reassigned to changed bytes.

## Proposed single-attempt sequence

1. Complete dependency pins, actual phone availability and CPU readback, the
   existing account credential acquisition, port checks and other slow
   preparation. Do not change phone CPU or refresh settings. Do not infer
   availability from an old checkpoint.
2. Acquire the unchanged continuous `OwnerLanAdmission` reservation. Its exact
   trusted-certificate live witness must still be
   `503/udp_worker_unavailable`; verify the original gateway's PID/start/source
   and manifest, and all four original owned-role counts zero. A busy or unknown
   outcome stops the attempt.
3. Call `mark_owned_lan_starting()` before Popen, start only the new owned LAN
   listener with finite process runtime, and validate its exact listening
   event. Retain the reservation throughout the rest of the attempt.
4. Install the matching already-built gold39 helper only after its absent/busy
   gates. Prepare the existing account's bounded private input by ADB stdin,
   with target UID,0600 and restorecon, as in the reviewed supervisor. Do not
   create a host password file, a temporary account or a session descriptor in
   an argument/log. The normal App authentication remains mandatory.
5. Start a new explicitly opted-in owner driver. It performs its existing
   formal/target/private-input checks and marker cleanup. At the final
   pre-instrumentation point, synchronously call the frozen numeric source
   collector exactly once with its shared deadline at most15 seconds. No human
   ENTER, external file polling or source manipulation follows this call.
6. Accept only the closed numeric state gate below, then immediately start
   normal UI instrumentation. Record actual launch timestamps. If the final
   launch-boundary freshness check fails, enter owned cleanup; do not start a
   retry or silently accept an older observation.
7. During bounded startup, qualify the first source capture against the same
   native clock as described below. Only a qualified first startup may enter
   the one30–45-second steady trace. Do not reuse the source gate to certify the
   helper's later short reconnect as a second fresh-source performance cohort.
   Reconnect may remain a separately labeled lifecycle check.
8. Complete the original normal cancel/reconnect and cleanup contract. Keep the
   original reservation until the actual same-Popen listener exit0, one valid
   shutdown/quiescence record, owned media exit and fresh original role-zero
   snapshot are all confirmed. An uncertain cleanup retains the supervisor and
   reservation for remediation; process exit is not a retained lease.

The new supervisor should allocate its finite runtime from the reviewed
readiness, helper/input preparation, source-read, instrumentation and cleanup
bounds. For one30-second steady window, the existing instrument budget is120
seconds; for45 it is135. There is no reason to retain a180-second human wait,
but removing it does not justify shortening actual producer cleanup bounds.

## Closed numeric source gate

Use an explicit evidence kind such as `numeric_playing_format_unknown`, never
the current `public_watch_UI_format_unknown` visual gate. Require:

- known exact Morphe PID, UID and start tick before and after, equal across the
  collector bracket; bounded integer fields, excluding booleans;
- an unambiguous active MediaSession owner matching that exact PID/UID at both
  observations, with state3 and speed1 at both ends;
- `all_children_reaped=true`, successful identity/state reads, bounded output,
  and all collector waits complete before launching instrumentation;
- source collection after this listener's actual ready event, with ordered
  fresh timestamps and no prepopulated or previous-attempt result accepted.

`playing_state_bracket` is a MediaSession state observation. An unchanged
position/update does not alone prove a stall; a changed position does not prove
decoded motion. `reported_position_changed` may be reported but is not a
required motion surrogate. A missing/paused/ambiguous state fails this source
gate promptly. Do not automatically force-stop, reopen, seek or change renderer
to make it pass.

Keep `live_format_known=false`, format/MIME/itag/contentFPS/bitrate null, and
visual motion unknown. The quality menu, historical itag299 and pre-coldboot
PID5212 cannot fill those fields. A known playing state also cannot identify
BBB rather than another current Morphe item. Label the result as source-supply
observation of the current playing Morphe session, not a controlled same-format
A/B, V50, cellular, public-path or optical acceptance.

## Freshness boundary and clocks

The conservative source age begins at the first observation, not when its JSON
is copied. In the new guard wrapper, sample
`clock_gettime_ns(CLOCK_MONOTONIC)` immediately before and after the single
collector call. Keep those named fields separate from the collector's Python
monotonic timeout fields. Verify their ordering and collection duration; use
native CLOCK_MONOTONIC at the instrumentation launch boundary to enforce at
most30 seconds from the earliest source-observation stamp.

Launch freshness alone is not READY or first-media freshness. The existing f08
capture trace declares `host_clock_gettime_CLOCK_MONOTONIC_ns` and emits
`capture_enqueue` with `grpc_return_ns` and `enqueue_ns`. A candidate can inspect
only a bounded, complete, whitelisted prefix of this attempt's owned trace,
require its clock contract, and qualify the first capture against the guard's
native MONOTONIC bracket. These timestamps prove first capture admission, not
phone reception/presentation or decoded content motion. Do not use the
packetizer's UPTIME timestamp, source Unix PTS, guest elapsedRealtime or an
unverified Python clock offset for this subtraction.

If the first qualified capture is absent at the30-second freshness deadline,
has stale timestamps, or has an unknown clock contract, do not enter the
qualified steady window. Cancel only the current owned attempt and record a
fixed source-freshness failure with any partial trace labeled unqualified.
Never refresh a timestamp without a new observation. A bounded watcher must
not follow an arbitrary path, read raw stderr/session descriptors, scan
historical traces or race a different campaign. Existing source/phone SF
sampler barriers still determine the actual steady interval and coverage.

This is an admission/coverage contract, not a hard real-time claim about OS
Popen, device scheduling or diagnostic file delivery. Those failures must be
visible and fail qualification rather than be hidden by a claimed atomicity.

## Minimal implementation scope for the next heartbeat

- New `scripts/probes/owner_source_gate.py`: closed numeric validation,
  one-shot collector wrapper and native-clock freshness checks; no source
  control, authentication bypass or live registry replacement.
- Explicit default-off owner LAN option in
  `scripts/probes/run_authenticated_lan_ui.py`, with the source guard called
  after existing preflight/marker cleanup and before instrumentation; bounded
  startup qualification before starting steady samplers. Normal/default driver
  behavior and helper/App media algorithms stay unchanged.
- New `tests/test_owner_source_gate.py`, plus narrowly scoped additions to
  `tests/test_lan_ui_driver_cleanup.py` for hook ordering and owned cancellation.
  The completed CI fixes must remain intact.
- A new private supervisor copy under a fresh `/private/tmp` directory, reviewed
  and pinned separately. Preserve the frozen f08 host/native/packetizer/helper
  bytes and original runtime. Remove ENTER/source-file inputs in that candidate
  only; do not mutate the two historical supervisor files.

First implement/review offline fixtures; then freeze exact collector, guard,
driver, supervisor and helper/dependency pins. Root may execute one protected
real attempt if the phone and formal/owner gates pass. A failed numeric playing
gate is actionable source state evidence, not permission to run a media matrix.

Required fixtures cover: playing-but-position-unchanged; paused/unknown/foreign
or duplicate owner; process restart; noninteger/NaN/oversized input; raw-read
timeout/unreaped child; result predating listener; exact30-second boundary,
future and stale stamps; slow preflight before collection; guard failure with
no instrumentation Popen; stale/unknown/missing first-capture contract with no
steady sampler; second-session gate nonreuse; original role change; and
cancel/reap/quiescence failure retaining admission. Use owned fake children,
fake clocks and inert callbacks, not production listeners or service signals.

## Rollback and evidence limits

Rollback is disabling the owner-only option and deleting only this attempt's
private input/helper/markers after owned processes stop. No App downgrade,
guest restart, production gateway replacement, NPC migration, NPS restart or
node deletion is part of the change. Preserve current alpha8, original signing,
credentials, guest data and domestic route rules.

Report the actual source evidence kind, physical/stream dimensions, requested
settings, exact dependencies, path, startup/steady coverage, receive and
independent SF cadence/long tails separately. Numeric playing admission and a
host trace cannot by themselves demonstrate visual continuity, actual source
rendition, phone touch latency or acoustic audio/video synchronization. This
document contains design and read-only code inspection only; implementation,
offline tests and real-phone acceptance for the new flow remain pending.
