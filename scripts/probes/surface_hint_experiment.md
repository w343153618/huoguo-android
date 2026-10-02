# Probe-only Surface content hint experiment

The fixed restricted `udp-video-session.json` accepts optional numeric integer
`content_hint_fps` (0–240). Omit it to preserve the existing
`MainActivity.configure` path and inherited `maxFps` hint. Set it to `0` to clear
the Surface content hint, or to `60` for an explicit 60 FPS source hint.
The hint is applied to the same `SurfaceView` Surface immediately after existing
decoder configuration and before first codec input. Format, hardware decoder
selection, frame listener, `PlaybackClock`, timed output release and audio are
unchanged. Explicit hints fail the experiment if they cannot be applied.

First comparison: keep physical display request `display_hz=120`, stream target
`fps=60`, `buffer_ms=60`, `video_release="scheduled"`, real video source and every
transport option fixed. Run explicit `content_hint_fps=60` versus `0`, repeated
in reverse order. A hint is not a physical mode lock; require start/end mode
readbacks and independent SurfaceFlinger cadence to identify actual conditions.

Report fields are `content_hint_explicit`, `requested_content_hint_fps`
(-1 means omitted), `intended_content_hint_fps`, `content_hint_applications` and
`content_hint_application_status`. Actual display mode fields remain
`display_mode_start` and `display_mode_end`; decoder callback counts do not
measure physical presents.

Late-submit/output-lead experiments are deliberately not implemented in this
change: existing output ownership lives in `MainActivity.configure`; replacing
that whole decoder/output branch would mix another variable into this first
comparison. No production defaults or saved user parameters are changed.
