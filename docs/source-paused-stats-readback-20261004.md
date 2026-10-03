# Fresh paused source format readback, 2026-10-04

The source used in the previous real-phone sequence changed to another cartoon
by the final screenshot. That prevented a controlled content comparison. Root
has now returned to the observed official BBB result, paused the source at
6:17, and read its actual Stats for nerds fields. The new readback belongs to
this paused preparation; it does not fill the unknown format fields of earlier
media sessions.

The current native UI shows Video ID `aqz-KE-bpKQ`, video allocation
itag299 / avc1 / 1920×1080@60, and audio allocation itag251 / Opus. Its viewport
is 1080×608, cumulative dropped frames are 0/11319, and the visible play control
indicates paused. The 60 value is the stream format descriptor. It does not
measure decoder, compositor, capture, or phone presentation FPS. The source's
physical display remains 1080×1920 at30Hz, density480,6cores,8GiB, actual Vulkan.

The observed route through this installed Morphe UI is paused player gear →
More → Stats for nerds. The first gear-sheet row is Add to Morphe queue. Root
did not select that row. Two earlier controls/gear taps passed the protection
and cleanup checks but did not visibly open the menu; their guard success is
not UI acceptance. A bounded controls UI dump also returned no target, so it
was not treated as a settings or format readback. Pausing with the explicit
MEDIA_PAUSE key made the observed controls persistent enough to inspect.

Each source action used a fresh restricted private candidate and the existing
continuous reservation, exact original-registry503 witness, original
identity/four-role-zero checks, formal TCP checks, and final owned-quiescence
checks before releasing the reservation. The successful source-only actions
lasted3.503–8.883s; all their owned local clients were reaped and none timed out.
The source remained PID3470/UID10235/start1952 and the emulator remained48576.
No phone, M5, NPS, gateway, guest boot, resource or system property was changed.
The Stats overlay was explicitly enabled as a source App diagnostic and may
persist. The source is left paused; this prevents playback from advancing into
another video while preparing the next bounded experiment. Permanent autoplay
disablement has not been validated.

## Standalone parser and actual input

`scripts/probes/source_ui_stats.py` parses an already collected UTF-8 UI snapshot.
It opens no menu, launches no ADB or media process, and reads no credential. Its
CLI defaults to no collection. An explicitly supplied input must be an owned,
0600 regular file opened without following symlinks, bounded to1MiB. XML DTDs,
entities, excessive depth/node counts, foreign/duplicate Stats layouts or
fields, unexpected formats and an unexpected Video ID are rejected. The
closed output contains format numbers, public Video ID, optional viewport and
cumulative counts, player control state and the displayed position. It omits
sCPN, opaque audio-track tags, client-spoof suffixes, account data and raw XML.

Root ran34 focused Stats/quality/decoder observation checks in1.681s, then
parsed the actual59957-byte private UI snapshot. The typed output agrees with
the root-viewed pixels. The source-only candidates verified full PID/UID/start;
the extra read-only Stats dump bracket independently reread PID/start only.
The parser itself deliberately returns process_identity_verified=false: a
valid XML or JSON document cannot establish process ownership. The next
collector must independently bind fresh full identity, foreground/session,
snapshot and timing; this utility is not yet integrated into the live source
gate or App.

The numeric evidence is in [source-paused-stats-readback-20261004.json](source-paused-stats-readback-20261004.json).
Raw screenshots and XML remain in restricted private storage. There is no
new APK or manifest change. Exact prior commitcb942e0/run37149007874 was also
read back as overall success with both build and udp_candidate successful;
that result does not apply to this new parser commit.

## Next controlled observation

In a NEW frozen candidate, bind the fresh Stats fields to full process identity
and qualify the same public Video ID, actual format and a bounded playback
position before and after the phone window. Resume only after protected
admission, and pause or inspect again before the content can run out. Clear
the diagnostic overlay or explicitly record its presence and overhead; do not
silently compare its source cadence with an overlay-free historical session.
Then use one30s observation at the retained540×960/30cap/4M VBR/80ms settings.
Keep startup, steady state and cancellation separate. This preparation is not
a stable30, WAN, cellular, V50, optical, acoustic or same-format AB acceptance.
