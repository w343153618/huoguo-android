The installed M1 legacy framework prints its JUnit/status success before calling
UiAutomation disconnect. The default five-file snapshot contract therefore
continues to distinguish log/serialization success, the owned child's exit,
and actual remote UI retirement. This iteration adds an explicit, default-OFF
normal-framework-return receipt rather than treating an `OK` line as cleanup.

The read-only installed-framework audit used jar SHA
`7e35b4f6d866c6cbeec7802c6360f1775f081d1a2393e6047acb271d032476b3`
and DEX SHA `a7697e858f8a343f378b4b1578406d2932ec2636e6a89813081d26208fa85689`.
Exact RunTestCommand bytecode selects a custom subclass with `-e runner`.
Its installed `run(List, Bundle, boolean, boolean)` calls virtual `start()`
before System.exit(0). Normal `start()` calls wrapper disconnect and handler
quit before returning; the wrapper itself calls UiAutomation.disconnect before
return. These are installed source-contract observations, not a device runner
execution or proof of global service quiescence. The SDK37 public stub does
not expose this runner, so a narrow ABI shim is compiled separately and never
included among D8 program inputs. The output DEX independently defines exactly
four local probe classes and no framework runner replacement.

`RetirementRunner` accepts only one fixed Snapshot test, closed owned relative
path and disabled debug/monkey options. It captures the directory and runner
identity before entering the installed framework. After `super.start()` returns
normally it writes `retired`: schema, child PID, UID, start ticks, directory
device/inode and a normal-return flag. No finally block creates this receipt
on framework exceptions. The write uses exclusive/no-follow creation, private
mode, single-link checks, fd/lstat matching, fsync and unchanged directory/start
identity. A failed write can leave an incomplete file; strict binding rejects
it and must preserve the exact scope. No framework cleanup order or deadline
was changed, and a quit call is not a joined thread or a global cleanup claim.

The separate pure six-file binding requires started/waited/completed/log/XML
and retired metadata, the actual-child identity shared across the receipts,
normal zero wait, bounded successful log and disjoint private file inodes.
Its ownership, permission, scope-removal, source-qualification, installed
execution and remote UI-quiescence fields remain false. The legacy five-file
contract is unchanged. A dedicated native build explicitly accepts
`--snapshot-retirement` and fixed `-e runner`; the old observer's `--snapshot`
cannot accidentally select it. Neither candidate is wired into the App/driver.

Eight new checks and57 focused checks passed in5.028s. The actual production
classes compile against API37 plus the separate ABI shim. An actual JVM runs
the candidate Runner against an explicit fake framework/receipt backend:
normal return reaches the receipt, thrown cleanup does not, foreign test/path
never enters the framework, and unexpected run return fails. Those order
fixtures do not exercise Android Os, UiAutomation, binder retirement or actual
device permission. Numeric mismatch, incomplete/aliased files and missing sixth
receipt checks do not infer ownership from valid JSON.

Private explicit builds passed: JAR
`329cad9923e03a8e3fb9d152d12347e54b91433c7d921acdb102ecf74f731efd`
(9188B), arm64 native
`859e2756c1676ee3e87dbf2df8eb7715697cab094252aee34d00ed44efe145c4`
(17048B). Source/tool/dependency pins are in the accompanying JSON; artifacts
and private build directories stay outside Git. Old staged JAR and native
freezes remain historical and are not relabeled as retirement candidates.
There was no device UI, input, phone/media session, service restart or APK release.

Next integrate a separate six-file observation and append-only journal result.
Retain failed/unknown scopes; do not apply old three-file deletion or release
an unverified current App/source lease. Real execution still needs fresh M1
source identity after storage maintenance, protected admission/current App
authority and actual remote lifecycle checks. Keep3s command/6s phase/15s
collector, byte limits and original media settings. Public moving-video
diagnostics follow those gates; this work is not a UDP performance improvement.

Preceding bbf142f7/run37185638482 was independently verified overall/build/UDP
success. This candidate requires its own exact-SHA CI; preceding results are
not later-source or real-device evidence.

Exact a5bb8bf0/run37186685354 subsequently independently verified completed,
overall/build/udp_candidate all success. It does not establish real UI execution.
