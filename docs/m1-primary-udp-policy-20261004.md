# M1 primary host and UDP release policy

The user's 2026-10-04 instruction supersedes the previous M5-friend / M1-owner
split. Research, exploration, performance experiments and optimization now use
M1. Future stable releases must use authenticated UDP media and choose M1 for a
fresh installation. M5 remains a selectable backup/test host. Existing active
sessions, both guest data sets, independent NPC/Tailnet identities and services
remain protected during this change.

The experimental UDP UI's fresh/invalid saved scope now defaults to index2
(public M1), instead of index3 (public M5). Existing valid saved selections keep
their meanings and values. Labels identify M1 as the default and M5 as backup
/testing. The existing actual-Java preference contract fixture verifies both
fallback and retention. This is a source change for the next APK, not a claim
that an installed or published APK has changed.

Published stable1.31/code32 and public experimental1.31-alpha.8/code39 remain
historical artifacts until a replacement is built, signed, verified and
released. The owner OnePlus12's private code40 artifact is also distinct from
this new source. A stable UDP release still requires user-facing acceptance,
including phone playback/lifecycle and the host-file/LAN isolation gate for
friend/public use. Do not silently fall back to TCP media.

M1 remains6vCPUs /8GiB RAM /1080×1920 /density480 /30Hz, with persistent
Vulkan HWUI. M5 retains720×1280 /density320 /8vCPUs /8GiB /30Hz. The current
30Hz modes work, so the conditional fallback to60Hz is not triggered. APK-only
migration from M5 to M1 copies missing installed app packages, with actual byte
hashes/signatures/version checks. It does not transfer app databases, accounts,
login sessions, root grants, hooks or modules. Existing M1 apps are not replaced.

The unrelated formal NPS/NPC services must not be restarted for this policy.
Only future qualified M1 work is the optimization target; M5 is read-only when
extracting its installed app packages and is not used as a performance-test host.

The APK migration and M1 data-disk expansion10→32GiB are now complete; RAM
remains8GiB. See m1-app-import-storage-20261004.md/json for16 verified packages
and the distinct TikTok first-run/login/playback boundary.
