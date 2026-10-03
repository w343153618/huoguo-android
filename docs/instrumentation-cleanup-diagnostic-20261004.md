# Bounded cleanup diagnostics for the owner LAN driver

Failed instrumentation previously discarded stdout/stderr obtained only during
reaping, so a source_first_capture deadline could hide a phone-side launch
failure. The candidate projects closed numeric diagnostic fields from this
already captured output. It does not change the primary failure, exit code,
existing ownership checks, phone stop policy, terminate/kill/wait ordering or
private-input cleanup. Diagnostics never authorize ownership or media success.

Only one complete stdout line beginning with the exact numeric-result marker
is eligible. Combined UTF-8 size must be≤64KiB. Duplicate/truncated markers,
stderr result contamination, duplicate JSON keys, nonfinite numbers, wrong
types, invalid codes and malformed/oversize JSON are rejected. LF/CRLF lines
are accepted; bare CR and Unicode separators cannot fabricate a new line start.

Allowed output is byte/line counts, fixed instrumentation marker/error counts,
an int32 instrumentation code, strict helper-started boolean, closed exception
class/label sets and bounded connection codes. Unknown class/labels become
unclassified. Raw text, messages, arbitrary JSON fields, credentials and logs
are not persisted. Missing output stays unknown; helper-started is still only
a report and is not a phone reservation or ownership witness.

The64KiB bound covers parsing/projection. Legacy communicate() still captures
raw child output in memory before projection; this change does not claim a
hard memory bound on child pipe capture. Cleanup timeouts/read errors and a
diagnostic exception are recorded separately without replacing primary.

Independent review and root targeted checks passed41 tests (18 new plus23
existing source-driver/cleanup tests). Root full source/owned-fixture suite
passed1562 tests in60.140s. This is not new phone or cloud acceptance. The
completed auto5 experiment used frozen d887421d and cannot be retrospectively
assigned this diagnostic.

Exact reviewed candidate pins:

- scripts/probes/run_authenticated_lan_ui.py:5e5247bd6e4a0578a771c97928aa140c0984335df785648c8827f707ae68354c
- tests/test_instrumentation_cleanup_diagnostic.py:f8c10d46c16932cf27ee9cdf76fd3adab0003c5b92b813d46803d36791765e13

No App, manifest, persistent runtime, NPS or device was changed by this patch.
