# Explicit current-playing-source Pause and readonly reader candidate

This iteration completes the **source and build integration**, not runtime
UiAutomation, remote Pause or a new phone performance result. Public alpha8,
owner phone code40, media defaults and production services are unchanged.

## Result and contract

- `--source-input pause-only` is a new explicit helper/driver mode. It requires
  M1, nps_owner, saved-UI authentication, media-only and an exact source
  PID/UID/start identity. A fresh playing target from the same qualified Stats
  snapshot is required. Transaction phase1 is distinct from the semantic Pause
  action2: the marker nonce still binds the current captured App Attempt.
- A same-identity independent paused-state observation and fresh paused Stats
  endpoint must succeed before the matching verified nonce is published. The
  helper still uses its existing native DOWN/UP/conditional CANCEL path and
  main-thread ownership checks. No stale coordinate, guest input or retry is
  substituted when qualification fails.
- This mode leaves its one normal UI session, verifies its ended codec/audio
  path, and finishes. It creates no steady marker or SF sampler and performs no
  reconnect. The driver's steady acceptance fields are null. Normal native
  mode retains its two ordered phases and actual sampler barrier.
- The direct hierarchy reader requires explicit `--source-snapshot-deployment`
  in pause-only mode. Default source and media paths remain unchanged. Its
  descriptor is a closed eight-field JSON object in a caller-owned0600 regular
  single-link file; nofollow, pre/post metadata, byte limits and duplicate
  rejection apply. It is configuration, not authentication or a guest lease.
- The reader rechecks the deployed JAR before use, binds the existing source
  identity/focus/MediaSession/geometry bracket, and exports numeric completion
  and retention information. A failed or empty/truncated ADB response now
  retains the exact possibly-created scope even without a creation prefix.
  No old fallback deletes beneath an unconfirmed remote runner.

The3-second UI command,6-second source phase,15-second collector ceiling,
1MiB per dump,4MiB total collector, and helper's existing bounded marker waits
were not increased. PCM/startup/stage/lead/FIFO/buffer/media protocols were not
changed by this candidate.

## Checks and actual helper build

The final focused invocation passed **98 tests in6.392s**, including real
API37 all-source compilation and actual JVM checks for closed helper modes,
owner replacement, partial gesture cancellation, phase/nonce gating, pause
recovery versus steady acceptance, private descriptor rejection, reader
timeout retention, saved-UI and existing driver cleanup. An earlier command
named three nonexistent test modules; its import errors are retained in the
JSON evidence and are not classified as a production failure or a passed run.

The new privately frozen matching helper is78227B, SHA:

`9b1cc1434e45e58ad45f0787f8931783de84e48b78a2c5dbbd236eff92d19d05`

Its compile, DEX, link, alignment, signing and signature verification all
returned0. All106 class files match the frozen22f App classpath. The original
signer matches; target App SHA remains d0437e51…ecf08 and JNI4bf34fc5…cc280.
The new helper was **not installed**. The original a575 helper and old frozen
campaigns are historical; the new driver requires the new exact helper SHA.
No helper/APK/JAR/signing material is committed.

## Actual readonly evidence and remaining boundary

Independent readonly inspection confirmed the original gateway PID/start/
source/runtime and four-role-zero baseline, with phone target/helper absent.
The exact source3470/10235/start1952 focus, active owner and playing-state
bracket passed and all local queries were reaped. This did not collect current
video format or pixels and is not a source control lease.

The installed `/system/bin/uiautomator` wrapper is4173B, SHA
`588f4e89975e626c1ae185c350389e4fad005b2cb9525834a42dfa7dfd548545`.
Its final command uses `exec app_process`. That offers a concrete path for
tracking the owned launcher through exec before Java serialization. Reading
the script is not proof of an actual runner's setup, exit, suppression behavior
or cost. The staged a19c JAR was not executed.

**Do not execute the new direct UI route yet.** Its success path requires the
real runner's exit and exact owned removal, but a timeout before serialization
can still leave remote ownership/quiescence unknown. Before the first actual
read, finish and independently validate an owned startup/exit contract and
persist exact possible scopes before commands. The helper must not release a
source lease while its remote UI runner remains unconfirmed. Do not turn local
ADB reap, a valid JSON receipt, or a socket snapshot into remote quiescence.

Next continue that failure boundary, then one NEW current-source authenticated
readonly qualification/Pause recovery. Only subsequently run a fresh moving
video public window. No service signal, source input, App authentication/media
or SF sampler was performed in this iteration; M5 and all NPS clients were
untouched. No physical latency, audio, public stability or V50 claim is made.
