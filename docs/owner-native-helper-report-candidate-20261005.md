# Native owned helper JSON observation candidate, 2026-10-05

The default-OFF native wrapper now reads the unique numeric payload retained by
its own actual instrumentation child after natural wait and both pipe EOFs.
It constructs its own scheduler/owner graph; callers cannot replace the payload
with a supplied JSON object, digest, PID or old scope. The opaque-output prototype
is now canonical, with an exact producer-specific JSON reader. No device entry
or release/cleanup method was added.

The reader accepts the pinned single-window helper's 80-field ASCII schema:
normal saved-LAN restore flags and exact route, V50/30/80/lead0, stage/startup OFF,
one captured-Attempt observation, ordered phone samples, conservative click-to-
lease timing with the existing eight-second close margin, and the helper's
normal Back/dialog exit and audio-thread observations. Codec OFF output must
match its actual closed state (CLOSED=7), and the App-report SHA must be well
formed. All duplicate keys, missing/extra/conflicting/failure fields, floats,
nonfinite values, wrong boolean/integer/null types, escapes, unknown objects,
integer overflow, bad clocks and incomplete samples are refused. These are
producer-specific accepted literals, not a general-purpose JSON implementation.
The entire 64KiB input is refused above its bound, with at most512 nodes/depth8.
The maximum44-row valid producer fixture fits the bound. No report is truncated.

A stalled worker/callback count is retained as an observation. Counts are not
presented FPS or a physical-loss diagnosis. The helper's current-Attempt and
normal-exit flags are still claims read from its owned output; valid JSON does
not independently establish a current App hold, normal codec/audio/input cleanup,
operator permission, server admission or Android PM quiescence. The App-report
SHA is observed only. A fresh same-process native App-report FD/identity/hash
bracket, matching package/Attempt/phoneclock and independent real callbacks
remain required. There is no uninstall, retirement or release API here.

Local verification:23 new checks plus39 existing driver/control checks passed,
62 total in49.238s. Actual host child/wait/dual-EOF/control/output ownership was
exercised; all Android outputs and permission callbacks were synthetic. Tests
also compare the independent existing Python helper readbacks, retain stagnant
counts, cover maximum44 samples/64KiB, duplicate/escaped/nonfinite input,
constructor refusal, premature/cancelled/expired closure, missing footer and a
later Attempt. The additional actual missing-callback constructor check passed.
The initial23-check run had4 failures in4.032s because the newly written synthetic
fixture omitted the existing producer's first_media_transport_code field. The
closed reader correctly refused it; the fixture was corrected. This was not an
App, guest or Android runtime failure, and the first result is retained.

Locked NDK29/API30 arm64 compilation produced66000B,
SHA2562576fd8dc2e717711cf5a1cab57e7d342432f233c7cdfa32612fe30d739b9bee,
with11 actual source/header pins and fixture macros OFF. It was compiled only,
not staged or run on Android. It is not an App/helper/JNI build or release.
The source-only build receipt records the external private output path.

Phone get-state was queried once and remained device-not-found. There was no
repoll, installation, authentication, media, RPC or source input. Existing
App/helper/JNI/public releases and NPS/default buffering/guard boundaries stay
separate. On device return the unchanged reviewed f0/d043 frame-only qualification
still comes first. The next implementation is the matching native App-report FD
reader and then complete actual host/native qualification; no partial activation.

Exact source390c7e6559ac1fb6b2853152ed3a2d1a53e5b153/run37231105080
completed with overall/build failure and UDP skipped. Android compilation/lint
succeeded; Linux2203 tests/144.342s had1 error and11 existing skips. An older
host early_DRIVER_DONE fixture wrote after the same correctly refusing native
parent exited, producing BrokenPipe before collecting its failure record. This
is a host test transport race, not Android/App/guest failure. The follow-up
source fixes only this expected rejection branch and deterministically forces
parent exit before the late write. It still requires the same Popen exit2,
unique refusal record and both actual EOFs; no skip/workflow/production guard
change or old-run rerun.21 driver checks passed31.656s, including the new race
regression; the two direct refusal checks passed2.587s. The failed source is
retained as failed; the follow-up source requires its own CI.
