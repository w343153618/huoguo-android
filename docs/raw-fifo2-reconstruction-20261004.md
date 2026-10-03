# Complete raw FIFO reconstruction and the next single factor

This is an offline reconstruction of the existing qualified [real LAN window](source-paused-endpoints-real-LAN-20261004.md), not a new phone experiment, APK, runtime deployment or default change. The original limited OnePlus12 + nonisolated M1 RAM8 used 540×960 / 30 FPS cap / 4 Mbit/s VBR / 80ms, AAC on, lead0 and FIFO2. Source paused descriptors matched BBB /299 avc1 /1920×1080 with a60 FPS descriptor; full-window continuous content and overlay overhead remain unverified. The original phone CPU limits drifted. Nothing here establishes V50, public NPS, cellular, physical touch or acoustic latency.

The inert [analyzer](../scripts/probes/raw_queue_reconstruction.py) reuses the bounded, closed-schema capture reader and requires complete capture sink/producer coverage. It reconstructs enqueue and dequeue operations by timestamps taken inside the same condition lock. It checks contiguous unique capture sequence, exact screenshot PTS identity, RGBA dimensions, capacity2, replacement identities, actual FIFO selection, raw-loop/budget pairing and queue depths. Equal timestamps, missing/duplicate events, non-FIFO skips, failed loops, shape conflicts or unaccepted coverage refuse all age distributions. It does not combine Python budget-clock values with native MONOTONIC timestamps; PTS is identity only. Native/phone coverage remains independently unknown.

The complete recorded queue reconciles1057 enqueues,1056 real dequeues and one capacity replacement, ending empty. This whole-session replacement is distinct from the measured steady window's zero replacement delta. Within the previously qualified host window,917 pairs reconstruct without conflict:

| Pending complete frames at the observed dequeue | Decisions | Observed FIFO age median | Newest alternative age median |
|---|---:|---:|---:|
| One |744|26.660ms|Same frame; no alternative|
| Two |173|35.201ms|2.378ms|

These groups are depths **at dequeue**. The older private pair subset grouped by depth **at enqueue**; its similarly sized groups have different members and must not be overwritten or treated as the same cohort.

All917 observed queue ages have median28.358ms and max39.558ms. For the same matched frame/loop, intersection with that loop's actual budget-wait interval has median27.407ms; the part outside that matched wait has median0.102ms and max8.007ms. These are intersecting host regions, not additive CPU costs, native slot measurements or source-to-phone latency. They do not prove that disabling pacing is safe, that all historical stalls share this mechanism, or that a30Hz source can deliver60 distinct frames.

At173 of917 historical decisions (18.87%), a newer complete raw frame was already pending while FIFO selected the oldest. Independently selecting that newest frame **at those same recorded times** gives a per-decision age difference median33.028ms for the173 changed cases. Across all917 decisions, including744 unchanged zeros, the difference median is0 and mean6.2033ms. Although the hypothetical age distribution median becomes3.591ms, subtracting that median from28.358ms would misrepresent the typical improvement. The analyzer evaluates one historical decision at a time; it does not clear its queue, change later decisions or predict a new run's FPS/latency.

The next useful single factor is an explicit, bounded owner-only `latest` raw-frame selection, with FIFO retained as the default. Complete RGBA frames can be superseded before encoding; encoded H.264 dependencies must retain their existing recovery logic. In a real latest run, clearing older frames can alter later empty reads, budget phase and delivered frame rate. Compare actual raw age, intentional pre-encode skips, source supply and independent phone cadence; do not equate these skips with lost UDP packets. Keep size, cap, bitrate, buffer, wait/guard, native slots, App and CPU settings unchanged, and retain current formal-session protection. Do not extend this into a broad matrix or promote it from the historical counterfactual alone.

Twenty new inert cases plus24 existing host-analysis cases passed (44 /0.140s). The actual frozen capture file passed the analyzer and its closed numeric output is in [the typed result](raw-fifo2-reconstruction-20261004.json); raw private traces are not committed. Exact preceding source commit2486952/run37155723250 was independently read as overall/build/UDP success. That CI result does not cover this new analyzer's subsequent commit or an APK release.

Example for a private frozen trace, replacing both endpoints with that attempt's qualified host MONOTONIC sampling window:

```sh
python3 scripts/probes/raw_queue_reconstruction.py \
  --capture /private/tmp/OWNED_ATTEMPT/capture.jsonl \
  --window-start-ns START_NS --window-end-ns END_NS
```

The CLI only reads the explicit bounded regular file, emits whitelisted numeric output, and exits2 on refusal. It neither reads credentials nor opens a listener or controls a device. The public NPS path still needs a separate admission design: the LAN experiment's45965 reservation intentionally prevents the existing public worker from starting, so copying that reservation into a public test would block the test itself.
