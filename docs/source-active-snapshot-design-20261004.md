# Active-source UI snapshot: installed-platform audit and next bounded probe

This is a readonly installed-code audit and a design, not a new collector,
source control, App artifact, performance result or acceptance.

Current source is playing after the first authenticated native Play attempt.
The next Pause must use a fresh qualified target/current authenticated Attempt.
Repeated old `uiautomator dump` on a moving Stats overlay had exceeded the fixed
3s command bound. Read the exact M1 guest platform implementation to distinguish
a tooling wait from UDP/codec performance before changing that bound.

Readonly `/system/framework/uiautomator.jar` readback412886B SHA
7e35b4f6d866c6cbeec7802c6360f1775f081d1a2393e6047acb271d032476b3;
classes.dex SHA a7697e858f8a343f378b4b1578406d2932ec2636e6a89813081d26208fa85689.
Local SDK37 public declarations and actual installed DEX were independently
inspected. No source App, setting, UiAutomation session, UI input or media changed.
Binary/tool details are retained privately, not committed.

Installed `DumpCommand.run` explicitly calls `UiAutomation.waitForIdle(1000,
10000)` before `getRootInActiveWindow` and serialization. A continuously active
accessibility tree can therefore conflict with a3s caller bound. This is a
concrete tool-contract mismatch candidate, not a measured explanation for every
historic timeout or for the immediate native-input transition refusal.

Installed legacy `UiDevice.dumpWindowHierarchy(String)` follows a different
path: `QueryController.getAccessibilityRootNode`→bridge root retrieval then
serialization, without the CLI's explicit idle wait. It writes beneath
`/data/local/tmp` with its supplied relative filename. SDK37 exposes that method
and `UiAutomatorTestCase`/`uiautomator runtest`. This supplies a possible standalone
readonly reader path. Do not assume that runner setup, root retrieval,
serialization, accessibility suppression or cleanup is free or bounded solely
because the explicit CLI wait is absent.

Next isolated candidate, default off:

- A small standalone test-runner JAR that **only** dumps the active hierarchy to
  a closed unpredictable owned0700 device directory, validates its owned
  namespace/file/size, and emits a closed numeric completion receipt. No click,
  key, swipe, menu, watcher, source-App attach/restart, preference, hook or
  credential call. Root retrieval alone may return null or incomplete data.
- Host per-command3s and total bounded collector budget stay fixed, output
  ≤1MiB and total≤4MiB. XML stays in memory and only existing closed Stats/native
  target parsing is exported. Source PID/cmdline/UID/start, full focus and active
  MediaSession owner/state, display/rotation remain independently bracketed.
- Completion must include actual runner return/receipt, owned device temp
  removal and all local children reaped. A timed-out local ADB process does not
  prove remote runner completion; query its exact role before deleting owned
  files. Refuse existing/foreign JAR/temp identities. Separate install/deploy
  scope, dependency SHA and rollback/cleanup from the readonly sample result.
- Compile/API/closed path/status fixtures before one source-only readonly live
  probe under formal/owner protection. Do not input or call it a Pause until the
  resulting fresh native target and current App Attempt separately qualify.
  Observe actual collection cost and failure boundary, preserve unknown results.

The JAR/collector is not implemented, built, deployed or run yet. Keep the
existing paused-reader/default driver unchanged, do not retry stale coordinates
or old paused-first media campaigns. Public/M5/NPS services and artifacts remain.
