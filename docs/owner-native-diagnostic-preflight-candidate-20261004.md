# Owner native diagnostic preflight — 2026-10-04

The normal NPS-owner HTTP settings do not accept `diagnostic_events`, and the
canonical registry selects false. The historical direct phone probe flag is a
separate entry point. The new numeric exporter therefore needs an explicit
trusted experiment path and a matching newly built App before live collection.
This candidate implements the first inert qualification component; it does not
wire an opt-in into a gateway or enable a native event ring.

## Closed local contract

`scripts/probes/owner_native_diagnostic_preflight.py` accepts a trusted local
plan, restricted to M1, emulator-5556/RemoteAndroid17Compare, LAN and isolated
45560/45963 endpoints. NPS, Tailnet, M5, public/formal ports, unbounded process
budgets, samples exceeding30seconds, unknown fields and coercible types are
rejected. Process budgets30–3600seconds are declared expectations, not an actual
wall-clock termination guarantee. These values do not prove listeners are free
or provide admission; existing exact live protections are still required.

The plan copies exact source/APK/version/JNI/helper/original-signer expectations
into a frozen value. Direct construction rechecks the same bounds. Public
alpha8, installed22f/d043 and historical local030126da lack the new export and
are explicitly ineligible. This is a known-old-byte exclusion, not a complete
classifier of arbitrary APK contents. A caller must independently establish a
new build's source, compiled exporter, signer and actual phone readback.

`qualify` defaults OFF with no device/file/network reads. With a parsed plan it
still does not inspect supplied readback until the caller explicitly qualifies
its provenance. Exact matching can return `artifact_pins_match_only`; version,
helper or matching JSON alone cannot qualify the App. Artifact mismatch, malformed
readback and missing provenance remain distinct. Valid JSON cannot establish an
operator, current App Attempt, private evidence, server guest lease or actual
native schema/report. `sample_eligible` and `diagnostic_events_requested` remain
false on every path because live integration is still pending.

No HTTP/account/environment switch is added. Existing native256/Java8192 rings,
64-row remaining prefix and whole64KiB report rejection remain. Native-only
qualification does not force host raw/feed tracing, which would confound its
future overhead measurement. The future coordinator must preserve80ms/FIFO/
sessionFPS/lead0/startup-stage-PCMqueueOFF/AAC and existing wait/guards.

## Actual validation and local artifact

Fourteen new fixtures and existing in-memory LAN/NPS session checks passed90
checks in0.388s. After adding the explicit unknown schema flag and excluding the
historical local App, the14 pure new checks passed0.002s. Cases cover default
inertness, strict scope/budget/pins, old artifacts, malformed readbacks, caller
mutation and direct-construction bounds. An actual in-memory registry create
keeps diagnostics false despite HTTP/environment requests and a successful local
preflight; the NPS-owner parser rejects an HTTP diagnostic field. No live service,
phone or native/JNI sampling was used by these fixtures.

A separate exact3d476 freeze of1048 tracked source files subsequently built
code40 alpha9 debug/release and release lint with exit0. The same freeze built
the UI helper against106 actual App classes, then both Apps and the helper passed
original-signer checks. The generated Java source matches the frozen exporter,
and compiled `numericNativeFrameEvents` is present. All1048 frozen files still
match after the build. Exact artifact identities are in
[native-frame-numeric-local-build-20261004.json](native-frame-numeric-local-build-20261004.json).
This is local compiler/DEX/link/signature acceptance, not ART/native report or
sampling-cost acceptance. Full JNI bytes changed with this build; native source
and dependency lock are unchanged from22f. No full-byte reproducibility or new
native algorithm is claimed.

The release App is f97b3e37/7245582bytes, JNI447f4928 and matching UI helper
ec805aed/82323bytes. They were not installed or published. Prior installed
code40d043/JNI4bf and public alpha8a663/JNI981 remain separate. The existing
reviewed f0 frame-only campaign stays unchanged and expects its original App/
helper pins; these new artifacts cannot silently replace them. Helper late typed
completion observation, report rejection and actual one-hour soak remain separate.

## Next protected step

Fresh phone readback is currently unavailable (`device-not-found`), so no helper,
authentication, media, RPC, source input or UI runner was started. Do not infer
absence of phone processes. When available, the original reviewed frame-only
attempt still requires its own fresh pins and normal saved-UI current Attempt.

For native events, first integrate a new explicit independent bounded M1 LAN
coordinator with the new exact App/helper pins and private evidence. Actual
operator permission, formal/original protection, admission and current Attempt
must be established outside this metadata validator. LAN's continuous45965
reserve cannot be reused to test the original public worker: it blocks that
worker. NPS public diagnostic selection needs a separate qualified high-port
entry design; its original persistent default remains false. Local builds and
these tests do not resolve the earlier moving-public source30/phone20 stall or
qualify MTK/V50, cellular, optical/acoustic, presentation or isolation safety.
