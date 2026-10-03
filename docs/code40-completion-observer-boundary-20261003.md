# Code40 completion observer: frozen helper-only boundary

The frozen alpha9/code40 App has no safe, complete helper-only installation
contract for observing its typed completion callback. Consequently no reflective
callback wrapper was added to the helper, and no App, APK or host source was
changed. The matching offline helper documented in
[code40-owner-helper-build-20261003.md](code40-owner-helper-build-20261003.md)
remains available for its existing normal accepted-report path. That helper does
not claim receipt observation or report-rejection acceptance.

## Why the owned Attempt lock is insufficient

In `experiments/nps-transport/phone/UdpVideoProbe.java`, `appListener` is a private,
ordinary nonvolatile field. `startApp` installs the original listener before
`new Thread(runner::onStart).start()`, which publishes that initial listener to
the runner. The terminal callback later reads the field and invokes it without
a shared callback lock or an observer-install handshake.

In `app/src/udp/java/local/remoteandroid/direct/AuthenticatedLanUdpUi.java`, the
authentication thread calls `startApp` and publishes the returned receiver
while holding its UI `lock`. That lock protects `current`, `retiring` and the
Attempt identity; it does not protect the runner's callback field or selection.
The original callback can already be selected before it calls `finished`.
`finished` performs storage and `finishRemote` before acquiring the UI lock to
publish retirement, so a helper may still see its captured Attempt as current
even though the callback was already selected or entered.

A late reflective field replacement and successful reflective readback therefore
do not prove that the real runner will invoke the wrapper. Holding the UI lock,
checking received-frame progress, or immediately calling `cancelApp` does not
provide a callback-selection handshake. A volatile cancellation write can
provide ordering only for particular later reads; it cannot retroactively
replace a callback already selected or running. The frozen code also provides
no retained typed receipt that a helper can safely read after an accepted report.

This prevents a complete no-missed-callback acceptance claim. The existing
runner's cleanup still runs through its original listener; we do not introduce
an unsynchronized reflective mutation merely to obtain a diagnostic.

## Deterministic offline evidence

`tests/test_udp_completion_observer_boundary.py` extracts the actual terminal
callback expression, actual `CompletionReceipt` class and `AppListener` signature
from the current frozen source. A narrow JVM `Result` double pauses argument
evaluation after Java has selected the callback receiver. This pause represents
one legal scheduling boundary; it is not evidence that Android currently hits
that exact interleaving or that its framework `Bundle` blocks there.

The fixture demonstrates:

- A late installer can still see the captured owner as current, replace the
  field, and successfully read back its wrapper, while the selected original
  callback receives the extracted receipt once and the wrapper receives nothing.
- Installing the observer before `Thread.start` instead publishes it to the
  runner, and both the observer and original delegate are invoked once.
- The frozen source has the pre-start original-listener assignment but lacks a
  late-observer or retained-receipt contract.

The three checks passed using the existing JDK:

```sh
python3 -m unittest discover -s tests \
  -p 'test_udp_completion_observer_boundary.py' -v
```

The run reported3 tests in0.560seconds. It does not execute Android media,
resource cleanup, instrumentation, network authentication or UI retirement.
Fixture receipt input values are explicitly component inputs; they do not
certify any real session's audio cleanup or expiration. No device operation or
new helper build was needed because the actual helper was left unchanged.

## Minimal safe interface for a future candidate

A later App candidate can provide an explicit owner-only observer before the
runner thread starts. The UI should capture an immutable observer registration
for the specific Attempt and generation before its normal Start flow, create
the wrapper before calling `startApp`, and pass the wrapper as the existing
listener argument. It should not change that listener after the thread starts.
The default registration is absent, so ordinary and existing helper behavior
remain unchanged. Registration must not be a generic global hook that can attach
to a later user's session.

The wrapper copies only the actual Probe-generated receipt's closed numeric
schema (`schema_version`, `audio_cleanup_state`, `statistics_report_status`,
`statistics_report_accepted`, `local_end_reason`, `requested_seconds`). Store at
most a fixed number of first/reconnect records, correlated by local owned
Attempt identity and generation, without endpoint, account, password, session
key, native pointer or arbitrary error text. The helper must reject missing,
duplicate, stale or mismatched observations rather than inferring them from the
performance report or thread names.

The observer does not fabricate or replace the receipt, alter its cleanup/end
reason, or decide UI retirement. Observer failure must remain separately
observable and must not prevent the original delegate from receiving the same
report, failure flag and receipt exactly once. Preserve the original delegate's
exception behavior. The collector should finish its bounded copy before the
delegate publishes UI retirement, with an explicit synchronized readback or
bounded completion signal for the helper. Its opt-in execution cost needs its
own scope label and does not imply zero sampling overhead.

Normal real-session cancellation and reconnect can then compare actual typed
receipt observation with the original UI result. An accepted report continues
to require full performance readback. A rejected report instead requires the
strict persisted `completion_receipt` schema, confirmed audio cleanup and
ordinary UI readiness, without pretending that rejected statistics are valid.
There is no oversized-report injection in this task.

Implementing that pre-start interface changes the App binary and requires a new
frozen candidate, source/APK/signature/JNI readback and matching helper build.
It cannot be retroactively attached as a proven contract to the current
`030126da...` candidate. Root can proceed with the current helper's existing
normal tests while this additional receipt acceptance remains pending.
