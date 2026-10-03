# Bounded Morphe source-selection review

The current VIEW intent resolves to the intended Morphe URL activity. The
requested public BBB URL appearing in an Android task is dispatch evidence,
not evidence that the player selected BBB. The previous actual screenshot
still showing a public cartoon is a failed selection check; do not replace it
with the desired URL or historical itag299.

## Actual read-only observations

The initial explicitly requested `emulator-5554` pass failed every command:
six nonzero command exits, zero raw bytes, 123.330333ms, and all local children
reaped. Its target was not silently changed. The parent subsequently confirmed
the request's serial was a typo and explicitly authorized `emulator-5556`.
This first failure remains separate from the successful observations below.

The correct-target first pass used six bounded read-only commands, completing
in 262.404041 ms with 107757 raw bytes. The follow-up `activity top` and
`media_session` reads completed in 106.460917 ms with 351852 raw bytes. Every dump
was below 1 MiB, with a shared 15-second reader budget and cleanup reserve per
pass. All owned ADB children were reaped. Raw text remained in memory and was
cleared; no account data, raw activity dump or raw metadata is retained here.
No activity was launched, touched or stopped by this review.

| Check | Current observation | Evidence limit |
| --- | --- | --- |
| Exact `pidof` target |One PID 3553 |This pass did not separately reread UID/startticks |
| VIEW resolve and query |Both select `app.morphe.android.youtube/com.google.android.youtube.UrlActivity` |Handler resolution, not selected video |
| Installed package filters |VIEW and HTTP/HTTPS filters present |Filter availability, not playback success |
| Activity task |Requested public BBB URI present; flags 0x14000000; target resumed |Stored/delivered intent can coexist with previous player state |
| Active target MediaSession |One active session, state 3/speed 1, position 0, update 8009363 |Numeric playing state; no live format or content identity |
| `activity top` |Target component present; no visible BBB video-ID/title field in this dump |No reliable current-video readback obtained |
| Target metadata summary |Metadata line exists but no BBB public ID/title match |Absence in an abbreviated dump does not prove a different video |

The resolver and task observations exclude a wrong default-browser/package
choice for this specific requested intent. They do not establish why Morphe
keeps the old player content. No launch-mode or patch-specific cause is proved.
Changing flags blindly or dispatching the same successful intent repeatedly
would add no independent source identity evidence.

## Next bounded selection method

Source preparation should happen before starting the fresh source-observation
deadline. Protect the original owner/formal session with the existing admission
guard before changing its player, then use one bounded native in-App selection:

1. Open Morphe's existing search control from a current screenshot. Search the
   exact public reference ID `aqz-KE-bpKQ` (or its complete public URL). Do not
   open another browser, clear App data, stop the player or introduce a hook.
2. Select a result only after independently reading its public BBB title and
   channel or content thumbnail. Requested text, the first result's position,
   and a successful input command are not selection acceptance.
3. On the resulting watch player, read the actual title and then one Stats for
   nerds overlay. The displayed video ID must equal `aqz-KE-bpKQ`. Record the
   actual codec/itag, current decoded dimensions and content-format frame-rate
   only when the overlay exposes those values. `1080p60` in a quality menu is
   not equivalent to those actual format fields.
4. Read an actual playback position in a motion segment, close the overlay,
   and obtain two independent player observations showing motion/position
   progress. Do not infer seek success from a URL's requested `t` parameter.
5. After the candidate listener is ready, run the existing exact
   PID/UID/starttick numeric guard again and retain its first-capture clock
   qualification. These freshness checks remain separate from source identity,
   format and motion acceptance.

Bound the UI sequence to one short attempt (for example 20–30 seconds), with
one result selection and one diagnostic-overlay read. If a search/result or
overlay cannot be independently identified within that bound, stop the source
selection attempt. Do not return to a repeated 180-second ENTER/menu loop.
The operator implementing these steps must preserve any current live session;
this read-only review did not acquire permission to interrupt one.

If this installed App's search cannot resolve the exact public reference, a
single alternate short public link is a dispatch candidate only. Root may
capture fixed status codes from `am start -W`, but even an explicit component
and ActivityManager success cannot substitute for the watch-player ID readback.
Do not presume that URL/task flags are the root cause.

## Acceptance and fallback

Accept a **known selected source** only when current watch-player content
identity matches the requested public video. Accept a **known actual format**
only when actual codec/rendition/size/FPS are independently readable; otherwise
keep each missing field null. Recheck source identity, format and actual
position after the bounded media sample before calling it a same-format
comparison. Unique decoded-content-frame and optical measurements remain
separate even when the overlay says 60fps.

If only the numeric MediaSession guard succeeds, the existing
`numeric_playing_format_unknown` supply experiment remains available. Label it
as unknown content/format and do not compare it causally with historical BBB
or call it a controlled same-format A/B. Keeping the pipeline fixed is useful
for stage observation, but does not control source supply.

This review changed no App/source preferences, VM resources, refresh rate,
phone, M5, NPS, authentication, cloud filtering, original gateway or media
service. It produced no new FPS, media-latency or source-selection acceptance.
