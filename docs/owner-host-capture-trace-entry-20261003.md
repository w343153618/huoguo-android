# Owner host timing trace entry candidate — 2026-10-03

Status: source/owned fixture wiring only, not a deployed gateway or measured diagnostic overhead. The published alpha8 App and current owner gateways remain frozen.

The LAN/Tailnet and owner-NPS server CLI now accept optional `--capture-trace-dir`. The default is `None`; it does not enable a native/capture/feed trace. Only a trusted operator CLI path is passed to the worker. An HTTP request cannot select the trace path: the NPS settings parser rejects the extra field, while the existing LAN normalization discards it instead of forwarding it. Neither behavior enables tracing. Authentication, scope/session admission, cancellation, original ports and gateway lifetime rules are unchanged.

The NPS CLI rejects trace opt-in with unlimited process lifetime (`--max-runtime 0`); its normal trace-off persistent mode is unchanged. LAN already requires a finite gateway lifetime. Per-file bounds and a finite test lifetime do not establish a global retained-directory size cap across many attempts. Operator-managed private test evidence remains necessary.

The worker candidate owns validation of an existing private parent and an exclusive attempt directory; trace path failure must reject the explicitly opted-in attempt rather than silently downgrade. The gateway's bounded listening record reports only `capture_trace_enabled`, not the filesystem path. A trace request does not allow arbitrary HTTP paths, guest commands or a different media endpoint. It does not assert host/LAN isolation.

The planned timing chain reuses bounded asynchronous Python/Swift capture trace and adds bounded writer/feed stage records. Missing condition wait, token-budget wait, control writes and loop gaps need observation before changing a queue, pool or raw submission cap. It is not a fix for the measured17FPS, and trace opt-in is not a default recommendation.

`tests/test_owner_capture_trace_entry.py` executes both actual `main` factory paths with fake TLS, registry, worker, threads and server. Five checks cover default-off versus CLI opt-in, request isolation, actual worker keyword propagation and the listening enabled bit. They start no listeners, read no credentials and run no media or devices. The first fixture attempt incorrectly patched a nonexistent `lan.sys`; it failed before main execution, was corrected to patch `sys.argv`, and the four checks then passed; a later lifetime-gate check also passed. These tests verify entry wiring; the worker's filesystem/trace and full concurrency overhead acceptance are separate.
