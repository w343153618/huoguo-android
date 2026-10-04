# Original dd43 registry fixture

These four `.py.txt` files are exact source bytes from commit
`dd43a39f49f6dceb55854fbc6e43367d34f381c3`. They contain source only, with no
credentials, session descriptors, live logs or runtime files. Their immutable
SHA256 pins are in `tests/test_owner_lan_legacy_registry_witness.py`.

The witness fixture compiles them in private module namespaces to verify the
original registry's pending-stop and cleanup-failure admission behavior. New
canonical registry changes must not substitute for this original contract.
Bundling the exact bytes also lets shallow CI/source-only freezes retain the
coverage without fetching Git history. Missing or changed bytes still fail;
there is no current-source or remote fallback and no additional skip.

These files are test inputs and are never production runtime deployment files.
