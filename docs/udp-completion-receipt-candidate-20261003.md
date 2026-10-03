# UDP completion receipt candidate — 2026-10-03

Status: offline source candidate only. This does not change the installed alpha8 APK, enable diagnostics, deploy a gateway, or prove phone/vendor audio cleanup. The companion report budget analysis is in `docs/app-report-budget-and-overhead-plan-20261003.md`.

## Problem and resulting behavior

The App previously inferred whether it could reconnect from `audio_cleanup_confirmed` inside the accepted numeric performance report. If `numericAppSummary` rejected the complete report above 65,536 UTF-8 bytes, it delivered an empty object to the App listener. The missing field then looked like unconfirmed audio cleanup, and the UI retained its retiring-attempt barrier even after real audio cleanup had succeeded. A statistics failure was incorrectly used as a resource-lifecycle result.

The listener now receives two independent outputs: the accepted numeric statistics object, if any, and a closed typed `CompletionReceipt` produced after the actual audio close attempt. The receipt is not inferred from the statistics object. Successful cleanup permits reconnect even when the whole statistics report is rejected. Unconfirmed or incomplete audio cleanup still retains the existing retiring barrier. No statistics are trimmed to fit, no absent statistics are called valid, and no transport falls back to TCP.

## Closed receipt contract

`audioCleanupState` is 0 unknown, 1 confirmed, or 2 incomplete. `statisticsStatus` is 0 unavailable, 1 accepted, 2 rejected by the 64 KiB check, or 3 rejected/failed for another reason. The receipt's numeric serialization contains schema version, these two integer states, the derived statistics-accepted bit, and the later closed local-end/duration fields described in `docs/udp-local-hour-completion-contract-20261003.md`. It contains no error text, arbitrary report fields, credentials, or UI content.

When a numeric statistics report was accepted, the UI stores that same object without adding fields or changing its byte size. When statistics were rejected or unavailable, the UI overwrites the previous report file with an explicitly named `completion_receipt` envelope. That small envelope is a control receipt, not a partial performance report. The existing 65,536-byte file bound remains in place. A write failure does not change the audio cleanup result.

A null receipt cannot authorize reconnect. A performance report containing `audio_cleanup_confirmed: 1` cannot authorize reconnect when its separate receipt is unknown/incomplete. The missing-audio case means this Probe attempt never constructed an audio receiver and therefore has no owned audio resources to retire; it does not claim audible output, hardware sound behavior, or measured lip sync.

## Actual audio cleanup readback

`UdpAudioReceiver.cleanupState()` is read after `close()`. Confirmation requires the close invocation to finish, no recorded codec/AudioTrack release exception, no live input/drain/PCM worker, no incomplete bounded PCM retirement, and no decoder, track, retiring resource, or PCM handoff reference. Release uncertainty is sticky for that attempt; empty pointers after a failed release do not count as confirmation. A thrown close or cleanup-state read returns unknown to the Probe and keeps reconnect blocked.

This adds readback and a release-failure flag, not a new scheduler or release algorithm. Existing join budgets, decoder ownership, bounded PCM reference retention, network STOP/DELETE, video worker termination, native destruction, UI generation protection, and session cancellation order remain. The receipt specifically describes owned audio cleanup; it is not a new verification of all video resources, remote gateway quiescence, host/LAN isolation, or vendor/native resource lifetime. Those acceptance boundaries remain separate.

The extra work runs during close/report completion. It does not change packet receive, frame admission, FEC/reference-chain logic, playback clock, media buffering, diagnostics default, or media transport. This source observation is not a measured zero-overhead claim.

## Offline checks and pending acceptance

`tests/test_udp_completion_receipt.py` compiles the real nested receipt/close policy, numeric formatter and UI reconnect/report-payload helpers with typed JVM JSON substitutes. It exercises the actual 64 KiB rejection, accepted/unavailable/invalid statistics crossed with unknown/confirmed/incomplete audio state, absent audio, close/read exceptions, unknown returns, missing receipts, report-field forgery, accepted-object identity, and numeric-only rejection storage.

The same test compiles the complete real `UdpAudioReceiver` with narrow Android API substitutes. It verifies successful and repeated close in both existing audio modes, codec/AudioTrack release uncertainty, an actually blocked bounded input worker retaining its resources until quiescent, and a live drain preventing confirmation. The existing actual receiver retirement fixture remains in place. These are Java ownership/control checks; substitute MediaCodec/AudioTrack APIs produce no real audio and do not validate Android vendor codecs, acoustics, or performance.

The 56 related offline checks passed, including all six new completion tests, the existing actual receiver retirement test, report budget/event/stage/startup checks, login-update and exit-confirmation checks. The three changed Java source files also compile together against SDK 37 and the existing App classes without building or installing an APK. A future candidate APK still needs source/signature/hash pins and real App validation: normal close then reconnect, deliberately rejected diagnostic report then reconnect, no statistics falsely reported, and an unconfirmed cleanup still refusing reconnect. Alpha8 and its historical reports do not acquire this new receipt retroactively.

## Follow-up: unpublished configure resources

An independent actual-Receiver fixture reproduced a missing exception path in both audio modes: `configure()` failed before publishing its temporary decoder, the temporary decoder's release then threw, the worker exited with null published references, and the original receipt incorrectly confirmed cleanup. The same branch could swallow an unpublished AudioTrack release exception. The candidate now sets the same sticky `cleanupReleaseFailed` flag for those two release catches. A release `Error` also marks uncertainty and preserves its original propagation rather than being swallowed. This does not change configure, ownership transfer, timing, buffering, scheduling, or normal retirement policy.

The additional actual-Receiver fixture supplies a failing MediaCodec configure followed by failed release, and a failing AudioTrack play before publication followed by failed release. It checks both legacy and bounded PCM modes with both release Exception and Error, and confirms that a failed configure with successful resource cleanup still permits confirmation; null published pointers and a later empty close cannot erase that uncertainty. The result is AUDIO_UNKNOWN and reconnect remains prohibited. This is offline Java exception/ownership evidence with API substitutes, not proof that a real phone vendor raises these errors or a measured device cleanup improvement.

Follow-up validation: all eight targeted completion/retirement checks passed, and the three actual receipt/receiver/UI Java sources compiled together against SDK 37. No APK was built, installed, or published in this follow-up. The earlier local alpha9 artifact does not contain this additional fix.
