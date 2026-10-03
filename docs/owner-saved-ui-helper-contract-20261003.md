# Owner helper: explicit saved-UI credential mode

This checkpoint changes the test helper and its driver only. It does not change
the released App, its encrypted password store, host accounts or media services.
No device, instrumentation installation or Android helper build was performed
for this checkpoint. The current published gold artifact remains
`1.31-alpha.8 / code39`; a release candidate at code40 and the existing debug
classpath at gold39 are separate dependencies. A matching helper must be built,
hashed and checked against the exact installed target before using the new mode.

## Invocation and credential boundary

`run_authenticated_lan_ui.py` keeps `--credential-source private-file` as the
default. Its original private-input and save/reopen/clear acceptance path remains
available. The new explicit `--credential-source saved-ui` is restricted to an
exact public owner node (`--network-scope nps_owner --node m1|m5`) and
`--credential-save off`.

An M1 media-only invocation, after independent host/session/dependency preflight,
is:

```sh
python3 scripts/probes/run_authenticated_lan_ui.py \
  --phone f7fc9469 --network-scope nps_owner --node m1 \
  --credential-source saved-ui --media-only \
  --stage-diagnostics off --steady-seconds 20 \
  --output /private/tmp/huoguo-owner-saved-ui-m1
```

For M5, use `--node m5 --phone-only-sampler` with `--media-only`; this driver
cannot verify an M5 source through the local M1 ADB identity. The selected control
addresses are exactly `146.56.249.175:49556` for M1 and
`146.56.249.175:49558` for M5. These are authenticated HTTPS session control;
the corresponding public UDP media addresses remain ports15556 and15558.
The route readback is not proof of the cloud transit or its domestic source.

Saved mode selects the normal App scope Spinner, waits for its posted listener
and password restore, then checks the actual selected scope, fixed address,
allowed username (`huoguo` or `wyw`) and nonempty credential length1..1024.
It repeats this sequence before reconnecting and revalidates immediately before
each normal Start click. It does not call the password store, read authentication
hashes or keys, convert the password Editable to a helper String, write a
password/account field, or pass credentials to instrumentation arguments.
The App performs its existing encrypted restore and normal HTTPS login.

The helper deliberately does not set the address immediately after
`scope.setSelection()`: that Spinner's callback is posted. An earlier address
write would run the normal TextWatcher before the new scope's username is
restored and could persist the previous scope's username under the newly
selected preference. The callback supplies the fixed public address instead.
Normal scope and media-setting UI changes still persist the App's usual
nonsensitive settings; saved mode does not save or clear encrypted credentials.

The store currently retains one server/account pair. A password saved for M5 is
not assumed valid for M1, or for another account. Missing, unlisted or mismatched
entries return a fixed bounded unavailable label without falling back to a
private file or changing an account. Saved mode never checks, reads, creates,
rewrites or deletes `udp-test-login.json`, including the early refusal and
cleanup paths. Helper output carries boolean verification, counters and fixed
failure classes, not username, credential length or password contents.

## Pre-instrument process protection

Both modes now check the exact target package
`local.remoteandroid.direct.experiment` **before** invoking `am instrument`.
Only an empty `pidof` exit1 with empty stderr proves absence. A running target
process is skipped even if it appears to be on its idle login page. Unknown or
malformed results are unavailable rather than treated as absence. No raw PID or
transport error is exported.

`--no-restart` is not enabled. A separate root-owned headless Android fixture
observed an unchanged self-package PID/nonce with `--no-restart` and a new
PID/nonce under default instrumentation, while the existing stable App remained
unchanged. This establishes that the CLI lifecycle difference matters; it does
not establish safe reuse of this App's Activity. `MainActivity.onStop()` cancels
its active `lanUdpEntry`, so even instrumentation that preserves a process could
interrupt a live session when it starts a new Activity. The absent-PID gate
therefore remains mandatory.

The PID check is not an atomic lock against another user starting the target
between the query and instrumentation. Use a bounded owner-controlled phone
window with no concurrent launcher or helper. Existing host formal-session
gates, the helper's `current`/`retiring` busy checks before UI edits and starts,
and per-attempt identity checks for cancellation remain in place. These checks
are not a blanket claim that an arbitrary live phone can be instrumented safely.
A future Activity lifecycle review must not relax the gate based only on the
headless fixture.

At a busy or missing saved entry, the driver does not force-stop either App
package. A reconnect refusal receives the same protection as a first-start
refusal. The helper's final cancellation is restricted to the attempt it owns,
including LAN/Tailnet paths. Pre-instrument saved-mode refusal touches no App
files; a launched helper can clean up only its usual phase markers. Existing
bounded cleanup for other failed owned instrumentation remains separate.

## Verification available and pending

Offline related fixtures passed72 checks, including12 new saved-UI checks.
They exercise the actual Python driver with inert subprocesses and extracted
actual Java validator/sequence methods with bounded fake clocks and UI doubles.
The password double throws if converted to a String, and programmatic field
writes throw before normal restoration. Cases include posted scope restoration,
reconnect, missing entry, unlisted account, wrong route/length, busy attempts,
strict boolean report readback, both-mode absent-PID admission, and no saved-mode
private-input access or force-stop fallback. Existing V50, exit-confirmation,
diagnostic opt-in, password-store and cleanup checks remain passing.

This does not verify Android Spinner delivery, Keystore restoration,
instrumentation/Activity lifecycle, actual media FPS, physical touch latency or
acoustic synchronization. Root must build the new helper from this source with
matching target classes, record helper/target/source hashes, verify target PID
absence and host idle state, then run normal saved-UI login and reconnect on the
authorized phone. An old helper ignores unknown instrumentation arguments and
could execute its private-file path; it must not be used for saved mode. The
driver's final readback rejection cannot retroactively prevent that old helper
from touching a file.
