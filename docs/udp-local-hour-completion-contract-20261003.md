# UDP local-hour completion contract — 2026-10-03

Status: independent offline source candidate. The local alpha9 APK built before this commit is a historical artifact and does not acquire the new receipt or atomic UI completion. No phone, gateway, media, account, NPS, or deployment was changed by this task.

## Independent control reason

`CompletionReceipt` now carries `localEndReason` and `requestedSeconds`, alongside the independent audio cleanup and statistics status. The source state is captured on entering `UdpVideoProbe.onStart()`'s `finally`, before audio/resource cleanup, performance report serialization or its 64 KiB acceptance decision. It comes directly from the existing receive-loop reason, `sessionLimitReached`, `appCancelled`, and a runner failure flag set by the existing outer catch. No rejected report field is read to infer the reason.

The closed values are:

| Value | Meaning |
| --- | --- |
| 0 | Unknown or not explicitly classified |
| 1 | Existing receive loop actually took its local deadline branch, with matching local-limit flag |
| 2 | Existing first-authenticated-video wait timeout |
| 3 | Existing authenticated-peer inactivity timeout |
| 4 | Local cancellation, when no prior explicit receive reason was recorded |
| 5 | Runner exception, when no prior explicit receive reason or local cancellation was recorded |

A recorded receive reason wins over a later cancellation or runner exception. Contradictory local-limit/reason pairs or unknown future source codes remain unknown. Merely requesting 3,600 seconds, reaching a catch/finally, accepting a report, or putting `session_limit_reached: 1` in report JSON cannot manufacture reason 1. Requested seconds are 0 if no valid session exists, otherwise the already validated 1–3,600-second duration.

The present media protocol supplies no authenticated server-expiry reason. A stopped server may appear as the existing peer timeout or another runner failure; this receipt must not call that “server one hour.” The existing local deadline remains unchanged: the receive loop's first authenticated video arrival and overall guard determine it. This control receipt is not proof of an uninterrupted hour of visible playback, measured latency, or server deadline agreement.

## One-hour reminder and statistics rejection

The one-hour reminder predicate is exactly local reason 1 and requested duration 3,600. The UI applies it only after the existing actual audio cleanup gate and generation/attempt completion checks. It takes precedence over report acceptance/rejection, so a genuine local hour still prompts rest when numeric statistics were rejected, invalid, or unavailable. Other durations, local cancellation, runner errors, first-video/peer timeouts, and unknown reasons do not trigger it.

The accepted statistics object remains unchanged. Rejected statistics still produce only the explicitly named small numeric `completion_receipt` envelope, now including the two new closed fields. No statistics are trimmed, invented, or called valid. Existing network-error/report-error display paths remain; this does not change session duration, authenticated UDP, anti-replay, cleanup budgets, media algorithms, or diagnostics defaults.

## Separate pre-existing exit race

An actual JVM fixture of the original `finished()` and `cancelOwned()` methods reproduced this order: confirmed completion clears `retiring`, queues its UI work, and then a cancel reestablishes the same completed attempt as `retiring`. The queued completion sees `current != attempt` and returns; no later Probe callback exists to clear that barrier. The observable result was no current session and the old attempt still retiring.

The candidate publishes confirmed completion atomically under the existing lock: matching attempt/generation, stopped flag, and clearing current/retiring are one transition. A cancel arriving afterwards sees no current attempt and cannot reestablish its barrier. Posted UI work uses the captured completion generation and requires no new current attempt, plus an Activity lifecycle check before display. A new session starting before that UI work cannot receive the old reminder or be cleared. A canceled session continues to retain its barrier until the actual cleanup callback; unknown or incomplete audio never bypasses it.

This is a small correction to the existing completion/exit transition, not an authentication or global generation redesign. It does not claim all vendor resource lifetimes or remote quiescence have been accepted.

## Offline verification

`tests/test_udp_hour_completion_reason.py` compiles the actual receipt, numeric report formatter, `finished()` and `cancelOwned()` methods. Only Android widgets/Handler/AtomicFile are narrow substitutes. The fixture executes actual 64 KiB rejection, accepted/invalid/unavailable statistics, local limit versus forged report fields, cancel/error precedence, other durations, unknown audio, cancel-before-confirmed, completion-before-cancel, new-start-before-post, late old callback after a new attempt, generation mismatch, and destroyed/finishing Activity cases. Outputs are bounded numeric receipts only, with no credentials, raw UI text or media.

The fixture substitutes do not produce real media, run Android Activity lifecycle, or validate vendor codec behavior. They reproduce Java control transitions; subsequent real App normal-close/reconnect, cancellation, delayed UI and hour reminder acceptance remain required. Installed alpha8 and prior alpha9 artifacts do not retrospectively contain these changes.

Validation receipt: all 75 related offline tests passed, including the eight new actual receipt/UI/cancel tests and existing completion, audio retirement, budget/event/stage/startup, login-update, exit-confirmation and host long-session checks. The three actual receipt/receiver/UI Java source files compiled together against SDK 37. No APK build/install or service operation was performed.
