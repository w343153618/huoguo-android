# Closed ART environment for the default-off owned source runner

The prior native candidate supplied only PATH, ANDROID_DATA and ANDROID_ROOT
to uiautomator. Fresh read-only M1 shell observations contain three ART roots
and two boot classpaths; stripping them is a startup compatibility risk. The
legacy CLI idle-wait/tool-budget conflict is a separate hypothesis. No Android
runtest has been executed, so this is not a proven cause of prior transition,
UI-dump failures or UDP playback stalls.

The new candidate validates and copies only ANDROID_ART_ROOT,
ANDROID_I18N_ROOT, ANDROID_TZDATA_ROOT, BOOTCLASSPATH and DEX2OATBOOTCLASSPATH
before any scope creation or fork. The three roots match the current fixed
system APEX locations. Each classpath is limited to4096bytes/128entries, with
nonempty unique .jar entries under /system/framework or
/apex/com.android.<module>/javalib. Traversal, nested paths, expansions,
control characters and foreign roots fail closed with code78. PATH remains
/system/bin, ANDROID_DATA /data and ANDROID_ROOT /system; no whole environment,
caller CLASSPATH, system-server classpath or unrelated values are forwarded.
Syntax qualification is not filesystem identity, caller authority or a lease.

Five new actual host C checks cover projection, absent/foreign roots, malformed
classpaths, byte/entry limits and validation before scope/fork. Along with the
existing owned-child/reader/UI-preference checks,33 tests passed in3.917s.
A separate host-only validator accepted the fresh actual M1 core values:
boot2913bytes/dex574bytes and the three exact roots. That validator did not
fork a child, execute an Android runner or open UiAutomation. Tests do not
claim zero overhead.

The locked NDK29 arm64/API30 build produced a16920byte private candidate,
sourcea869cc2e and binary2bf4b649; exact hashes are in the JSON evidence.
It has not been staged or executed on Android. The old fd770/39c954 build is
historical and cannot be relabelled as this source. Snapshot JAR stage and
all published/installed App artifacts remain distinct and unchanged.

The next gate is binding the actual owned parent started/waited journal,
child identity, Java serialization receipt and exact private scope under the
current authenticated App permission lease. Main-child wait/local ADB reap is
not whole UI-service quiescence. Preserve the3s UI/6s phase/15s collector
and1MiB/4MiB budgets; retain exact possibly-created scope on unknown failure.
No source App input, phone activity, M5/NPS operation, guest restart or media
session occurred. Existing M1 gateway identity/four-role-zero and absent
phone experimental client were independently observed before this offline work.
