# Offline combined host/phone UDP summary

`summarize_phone_udp.py` processes the JSON written by
`experiments/moonlight-v2/transport/android-udp/run_phone_udp.py`. It imports no
live runner and performs no ADB, socket, emulator, routing or credential action.
Call its pure `summarize(report)` function or run the CLI on an existing combined
report containing both a `host` object and a `phone` object:

```sh
python3 scripts/probes/summarize_phone_udp.py \
  /path/to/combined-report.json \
  --output /path/to/combined-report-summary.json
```

Replace the two paths with the root experiment's actual evidence paths. Omitting
`--output` prints the JSON summary. Output cannot overwrite the raw input, and
the input file is limited to 64 MiB. Host-only loopback/round-trip reports are
rejected explicitly rather than promoted to real-phone evidence.

The output separates these observations:

| Output | Meaning |
| --- | --- |
| `logical_media_receive_fps_over_receive_window` | Java complete logical media bodies divided by the actual first-authenticated-HGUD-to-receive-end interval; includes bodies dropped while waiting for IDR |
| `phone_counters` | Absolute packet, authentication/replay, received/queued-media, local-input-timeout, IDR-wait and codec callback counters |
| `native_fec_cumulative_counters` | Final native snapshot: packet rejection/expiry, completed/delivered bodies, recovered shards, dependency loss and IDR requests |
| `native_fec_final_gauges` | Gauges such as active mappings, current IDR need and maximum assembly latency; these are not counter deltas |
| `independent_local_stage_times` | Recomputed receipt→input, input→decoder-ready and decoder-ready→Java callback time from raw `System.nanoTime` fields |
| `codec_callbacks` | Java callback receipt counts, interval throughput and receipt gaps; **not actual display FPS** |
| `codec_timestamp_validity` | Strict revalidation of raw vendor times, including causally impossible future/before-release and requested-target echoes |
| `sample_statistics` | All per-second samples including startup, plus separately labelled full media intervals, distributions and threshold counts |
| `per_second_series` | Actual interval duration, rates and native cumulative-counter deltas; resets/regressions produce warnings |
| `host_packetizer` | Last cumulative source/packetizer summary, partial/final status, interval deltas and duration-weighted rates |

The tool recomputes codec stages from raw matched-frame records instead of
trusting stale aggregate arrays. It preserves invalid-stage counts. When callbacks
were evicted or retained-record count differs from the total counter, it labels
the timing as a retained span, not complete history. Actual display cadence still
requires root's independent SurfaceFlinger actual-present metadata.

Packetizer summary events and native samples contain cumulative values. The
tool does not sum those snapshots. It also keeps native expiry, dependency,
local input timeout and IDR-wait drops separate: these counters can overlap and
cannot be added into an invented global frame-loss total. A recovered **shard**
count is not a recovered or visibly displayed **frame** count. KEYFRAME request
and control-forwarding counts do not prove IDR arrival or visible recovery.

All phone samples remain represented. The `full_media_intervals_only` view uses
only intervals whose start is at or after the first authenticated HGUD packet
and whose end is at or before the receive window's end. This avoids treating
hardware startup/partial buckets as steady media throughput while leaving those
buckets visible in `all_samples_including_startup`.

Host send and phone receive counters can have different cutoff tails. The tool
reports their difference only with `is_network_packet_loss_measurement=false`;
the receiver count can also include rejected foreign datagrams. It cannot infer
a network loss percentage from those totals. Host and phone monotonic clocks
are not synchronized, so it never subtracts them as one-way latency.

The video-only component does not measure physical display FPS, acoustic A/V
sync or native touch. Its requested FPS cap is retained as a requested setting,
never substituted for measured receipt/callback throughput. A LAN OnePlus test
cannot establish WAN/P2P, cellular or V50 acceptance.

## Fake-data offline self-check

```sh
python3 -m unittest discover -s tests -p test_phone_udp_summary.py
```

The seven tests use explicitly fake numeric reports. They cover actual versus
requested FPS, future-target vendor rejection, raw stage recomputation,
startup/steady separation, cumulative-counter deltas, partial packetizer
summaries, duration-weighted rates, cross-scope packet-count limits,
non-finite/regressing observations, incomplete callback history and bounded
failure reports. They create no phone connection and claim no live result.
