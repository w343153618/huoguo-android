# Probe-only bounded late Surface submission

The restricted UDP Session JSON accepts optional numeric integer
`surface_submit_lead_ms`: `0` (also the absent default), `8` or `16`.
Nonzero values require `video_release="scheduled"`. The flag is not stored in
user preferences, exposed in the login UI or enabled for normal connections.
`UdpVideoProbe` saves the previous activity flag, applies the Session value and
restores it after stopping the experiment.

The existing shared `MainActivity.configure` and render loop are retained.
With a positive flag, the loop retains a decoded output until its unchanged
absolute clock target is within the requested lead. Each requested park is at
most 2 ms; remaining parks are capped at 80 ms from that output's original
decoder-ready time. On budget exhaustion the output is submitted with the
current original clock target, rather than adding clock delay or holding
indefinitely. An OS scheduling pause can overshoot a requested park, and actual
hold/park maxima are reported. Stop, generation and codec ownership checks are
repeated after each park. Existing late-output discard/catchup policy remains.

Decoder feedback (`videoDeadline`) is evaluated once for each newly dequeued
output. Subsequent park iterations use the pure `deadline` mapping read, so
waiting cannot repeatedly decay/advance decoder clock feedback or change shared
audio by counting the same output as multiple decoded frames. Audio code,
codec format, Surface listener and source PTS are unchanged. The disabled `0`
branch uses the existing immediate `releaseOutputBuffer(index,target)` policy.

Report fields:

- `surface_submit_lead_ms`: requested policy, `0` means disabled.
- `surface_submit_applications`: outputs that entered at least one park, not
  proof that a frame was physically presented or improved.
- `surface_submit_wait_count` / `surface_submit_wait_total_ms`: completed parks
  and measured elapsed park time, including OS scheduling pauses.
- `surface_submit_max_output_hold_ms` / `surface_submit_max_park_ms`: observed
  hold/park maxima; neither is measured optical latency.
- `surface_submit_budget_fallbacks`: outputs submitted before the requested
  lead was reached because the 80 ms hold budget was exhausted.
- `surface_submit_status`: disabled existing path, applied bounded wait, or
  enabled without an observed wait.

Compare `0` versus `16` with actual phone mode verified, the same 60 FPS source,
60 ms buffer, content hint, codec, audio, sender and trace options. Keep reverse
order replicates and independent SurfaceFlinger counts. A late submission can
also backpressure decoder input, so inspect inbox epochs, input timeouts,
explicit late discards and source cadence instead of assuming it helps.
Source-only checks are not a real phone or network performance claim.
