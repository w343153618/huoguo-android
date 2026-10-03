# Alpha8 beta login update action

Source checkpoint based on `78178e126a5f7ddb2eeef98e88c29775e773cd15`.

The experimental UDP login page now places its existing **检查更新** action at
the top right of a horizontal header. The title uses the remaining width and
can wrap; the action requests its natural width, 12 dp horizontal padding and
at least a 48 dp touch height. The existing login scroll root still reserves
system-bar insets. The version, channel and bounded trial description remain
below the header.

The button calls the existing `AppUpdater.check(true)` flow. This retains the
stable/test channel picker, release notes, confirmation, package/signature
checks and verified download path. It does not silently install an update.
Clicks on retained old views, a finishing/destroyed Activity, or an active UDP
session are ignored. No update action was added to the media surface.

Only the UDP login header changed. Server profiles, preference keys, stored
parameters, password encryption, endpoints and media handling are unchanged.
The button text remains **检查更新**, so existing label-based helper lookups do
not need a label migration. The formal UI and its updater implementation are
unchanged.

Validation executes the actual header method with inert Java widget doubles:
weighted layout requests, density-scaled touch dimensions, button click
routing and four stale/lifecycle guards. Existing profile, credentials,
insets and V50 policy checks are also run. These checks are source-level
evidence, not Android text measurement, an APK build, installation or V50
visual acceptance. Build, original-signature verification and publication
remain with the root task.
