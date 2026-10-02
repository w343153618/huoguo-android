"""Owned bundle fixtures; no authentication, accounts, SDK copying or VM actions.

Privileged invocations are mocks. Root payload validation runs only against
new private fixture bytes, never reads arbitrary paths from a manifest.
"""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.security import prepare_isolation_candidate as prepare


class PrepareFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="huoguo-prepare-owned-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.source, self.sdk = self.base / "source", self.base / "sdk"
        self.output = self.base / "bundle"
        self.source.mkdir()
        self.sdk.mkdir()
        self.helpers = self.source / "scripts/security"
        self.helpers.mkdir(parents=True)
        self.repository = Path(__file__).resolve().parents[1]
        for name in prepare.HELPERS:
            shutil.copyfile(self.repository / "scripts/security" / name, self.helpers / name)
        (self.source / "owned.py").write_bytes(b"OWNED_FIXTURE = True\n")
        (self.source / "second.py").write_bytes(b"SECOND_FIXTURE = True\n")

    def build(self):
        return prepare.prepare(self.source, self.sdk, self.output)

    def loader(self):
        namespace = {"__name__": "owned_loader_fixture"}
        data = (self.output / "root-loader.py").read_bytes()
        exec(compile(data, "<owned-loader-fixture>", "exec"), namespace)
        return namespace

    def test_prepare_only_pins_bytes_permissions_and_no_commands(self):
        with patch("subprocess.run", side_effect=AssertionError("no subprocess permitted")):
            result = self.build()
        self.assertFalse(result["executed"])
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(set(manifest["modules"]), {"owned.py", "second.py"})
        self.assertEqual(set(manifest["helpers"]), set(prepare.HELPERS))
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        for directory, subdirs, files in os.walk(self.output):
            self.assertEqual(stat.S_IMODE(Path(directory).stat().st_mode), 0o700)
            for name in files:
                self.assertEqual(stat.S_IMODE((Path(directory) / name).stat().st_mode), 0o600)
        for group in ("modules", "helpers"):
            for name, digest in manifest[group].items():
                self.assertEqual(digest, hashlib.sha256((self.output / group / name).read_bytes()).hexdigest())
        self.assertEqual(result["loader_sha256"], prepare.sha256((self.output / "root-loader.py").read_bytes()))
        self.assertEqual(result["command_sha256"], prepare.sha256((self.output / "command.txt").read_bytes()))
        self.assertEqual(self.loader()["_verify_bundle"]()["modules"]["owned.py"], b"OWNED_FIXTURE = True\n")

    def test_root_prepare_and_existing_output_refused(self):
        with patch.object(prepare.os, "geteuid", return_value=0), self.assertRaises(PermissionError):
            self.build()
        self.assertFalse(self.output.exists())
        self.output.mkdir()
        (self.output / "keep").write_text("preserve")
        with self.assertRaises(FileExistsError):
            self.build()
        self.assertEqual((self.output / "keep").read_text(), "preserve")

    def test_source_symlink_hardlink_fifo_and_size_refused(self):
        path = self.source / "owned.py"
        saved = path.read_bytes()
        outside = self.base / "outside.py"
        outside.write_bytes(saved)
        variants = (lambda: path.symlink_to(outside),
                    lambda: os.link(outside, path),
                    lambda: os.mkfifo(path),
                    lambda: path.write_bytes(b"x" * (prepare.MAX_FILE_BYTES + 1)))
        for create in variants:
            path.unlink()
            create()
            with self.assertRaises((ValueError, OSError)):
                self.build()
            self.assertFalse(self.output.exists())
        path.unlink()
        path.write_bytes(saved)

    def test_symlink_components_for_source_sdk_and_output_refused(self):
        alias = self.base / "alias"
        alias.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(OSError):
            prepare.prepare(alias, self.sdk, self.output)
        alias.unlink()
        alias.symlink_to(self.sdk, target_is_directory=True)
        with self.assertRaises(OSError):
            prepare.prepare(self.source, alias, self.output)
        alias.unlink()
        alias.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(OSError):
            prepare.prepare(self.source, self.sdk, alias / "bundle")
        self.assertFalse(self.output.exists())

    def test_module_names_total_and_count_bounds(self):
        (self.source / "evil name.py").write_text("fixture")
        with self.assertRaises(ValueError):
            self.build()
        (self.source / "evil name.py").unlink()
        with patch.object(prepare, "MAX_MODULES", 1), self.assertRaises(ValueError):
            self.build()
        with patch.object(prepare, "MAX_TOTAL_BYTES", 1), self.assertRaises(ValueError):
            self.build()
        self.assertFalse(self.output.exists())

    def test_changed_snapshot_or_manifest_paths_fail_before_privilege(self):
        self.build()
        loader = self.loader()
        (self.output / "modules/owned.py").write_bytes(b"CHANGED = True\n")
        with self.assertRaises(ValueError):
            loader["_verify_bundle"]()
        # Restore bytes then try to nominate a different root-readable path.
        (self.output / "modules/owned.py").write_bytes(b"OWNED_FIXTURE = True\n")
        manifest = json.loads((self.output / "manifest.json").read_text())
        manifest["modules"] = {"../../owned-outside.py": "0" * 64}
        (self.output / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            loader["_verify_bundle"]()

    def test_helper_and_profile_changes_are_pinned(self):
        self.build()
        loader = self.loader()
        for path in (self.output / "helpers/isolation_admin.py", self.output / "guest.sb"):
            saved = path.read_bytes()
            path.write_bytes(saved + b"\nchanged\n")
            with self.assertRaises(ValueError):
                loader["_verify_bundle"]()
            path.write_bytes(saved)

    def test_bundle_extra_files_hardlinks_and_symlinks_rejected(self):
        self.build()
        loader = self.loader()
        extra = self.output / "modules/extra.py"
        extra.write_text("extra")
        with self.assertRaises(ValueError):
            loader["_verify_bundle"]()
        extra.unlink()
        path = self.output / "modules/owned.py"
        owned = self.base / "owned-outside.py"
        owned.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(owned)
        with self.assertRaises(OSError):
            loader["_verify_bundle"]()
        path.unlink()
        os.link(owned, path)
        with self.assertRaises(ValueError):
            loader["_verify_bundle"]()

    def test_bundle_subdirectory_symlink_and_public_modes_rejected(self):
        self.build()
        loader = self.loader()
        (self.output / "modules").rename(self.base / "modules-outside")
        (self.output / "modules").symlink_to(self.base / "modules-outside", target_is_directory=True)
        with self.assertRaises(OSError):
            loader["_verify_bundle"]()
        (self.output / "modules").unlink()
        (self.base / "modules-outside").rename(self.output / "modules")
        (self.output / "guest.sb").chmod(0o644)
        with self.assertRaises(ValueError):
            loader["_verify_bundle"]()
        (self.output / "guest.sb").chmod(0o600)
        self.output.chmod(0o755)
        with self.assertRaises(ValueError):
            loader["_verify_bundle"]()

    def test_profile_has_fixed_candidate_outlets_and_no_production_runtime(self):
        self.build()
        profile = (self.output / "guest.sb").read_text()
        self.assertIn('(remote tcp "localhost:18131")', profile)
        self.assertIn('(remote udp "localhost:53")', profile)
        self.assertIn('(local tcp "localhost:5566")', profile)
        self.assertIn('(local tcp "localhost:5567")', profile)
        self.assertIn('(local tcp "localhost:8566")', profile)
        self.assertNotIn('(remote tcp "localhost:5037")', profile)
        self.assertNotIn('"*:53"', profile)
        self.assertNotIn(str(self.source), profile)
        self.assertIn(str(prepare.ISOLATION_ROOT / "homes/vm"), profile)

    def test_mock_root_invocation_materializes_verified_bytes_only(self):
        self.build()
        loader = self.loader()
        temporary = self.base / "mock-root-private"
        temporary.mkdir(mode=0o700)
        modules = temporary / "modules"
        modules.mkdir(mode=0o700)
        captured = []

        def materialize(contents):
            for name, data in contents["modules"].items():
                (modules / name).write_bytes(data)
            # Mutation of writable bundle after validation cannot change the
            # materialized byte snapshot given to provision.
            (self.output / "modules/owned.py").write_bytes(b"LATE_CHANGE = True\n")
            return temporary, modules

        def provision(source, sdk):
            captured.append((source, sdk, (source / "owned.py").read_bytes()))
            return {"phase": "staged"}

        admin = SimpleNamespace(ROOT=prepare.ISOLATION_ROOT, require_root=Mock(),
                                provision=Mock(side_effect=provision), write_new_file=Mock(),
                                update_state=Mock())
        loader["_materialize_root_snapshot"] = materialize
        loader["_helper"] = Mock(return_value=admin)
        with patch.object(loader["os"], "geteuid", return_value=0), \
                patch.object(loader["sys"], "platform", "darwin"), redirect_stdout(io.StringIO()):
            result = loader["_run"]()
        self.assertEqual(captured, [(modules, self.sdk, b"OWNED_FIXTURE = True\n")])
        admin.write_new_file.assert_called_once_with(
            prepare.ISOLATION_ROOT / "profiles/guest.sb", (self.output / "guest.sb").read_bytes(),
            mode=0o644, uid=0, gid=0)
        self.assertFalse(result["avd_cloned"])
        self.assertFalse(result["pf_changed"])
        self.assertFalse(result["services_started"])
        self.assertFalse(temporary.exists())
        admin.update_state.assert_called_once()
        self.assertEqual(admin.update_state.call_args.args[0]["guest_profile"]["sha256"],
                         loader["EXPECTED"]["profile_sha256"])

    def test_profile_failure_is_journalled_and_cleanup_preserved(self):
        self.build()
        loader = self.loader()
        temporary = self.base / "failed-root-fixture"
        temporary.mkdir(mode=0o700)
        modules = temporary / "modules"
        modules.mkdir(mode=0o700)
        state = {"phase": "staged", "avd_cloned": False, "isolation_accepted": False}
        admin = SimpleNamespace(ROOT=prepare.ISOLATION_ROOT, require_root=Mock(),
                                provision=Mock(return_value=state),
                                write_new_file=Mock(side_effect=OSError("owned fixture failure")),
                                update_state=Mock())
        loader["_helper"] = Mock(return_value=admin)
        loader["_materialize_root_snapshot"] = Mock(return_value=(temporary, modules))
        with patch.object(loader["os"], "geteuid", return_value=0), \
                patch.object(loader["sys"], "platform", "darwin"), self.assertRaises(OSError):
            loader["_run"]()
        self.assertEqual(state["phase"], "staging_failed")
        self.assertEqual(state["failure_class"], "OSError")
        self.assertEqual(state["staging_operation"], "guest_profile")
        admin.update_state.assert_called_once_with(state)
        self.assertFalse(temporary.exists())

    def test_loader_unprivileged_and_failed_verification_never_provision(self):
        self.build()
        loader = self.loader()
        helper = Mock()
        loader["_helper"] = helper
        with self.assertRaises(PermissionError):
            loader["_run"]()
        helper.assert_not_called()
        (self.output / "guest.sb").write_bytes(b"tamper")
        with patch.object(loader["os"], "geteuid", return_value=0), \
                patch.object(loader["sys"], "platform", "darwin"), self.assertRaises(ValueError):
            loader["_run"]()
        helper.assert_not_called()

    def test_outer_loader_pin_prevents_tampered_code_execution(self):
        self.build()
        payload = self.output / "root-loader.py"
        outer = prepare._outer_program(payload, prepare.sha256(payload.read_bytes()), os.getuid())
        payload.write_bytes(b"raise AssertionError('must not execute')\n")
        with self.assertRaises(ValueError):
            exec(compile(outer, "<owned-outer-fixture>", "exec"), {})

    def test_quoting_preserves_shell_metacharacters_and_payload_tokens(self):
        self.sdk.rename(self.base / "sdk '__BUNDLE__' $(owned); with spaces")
        self.sdk = self.base / "sdk '__BUNDLE__' $(owned); with spaces"
        self.output = self.base / "bundle '__EXPECTED__' with spaces"
        result = self.build()
        namespace = self.loader()
        self.assertEqual(namespace["EXPECTED"]["sdk"], str(self.sdk))
        self.assertEqual(namespace["BUNDLE"], str(self.output))
        self.assertEqual(namespace["_verify_bundle"]()["modules"]["owned.py"], b"OWNED_FIXTURE = True\n")
        argv = shlex.split((self.output / "command.txt").read_text())
        self.assertEqual(argv[:2], ["/usr/bin/osascript", "-e"])
        self.assertTrue(argv[2].startswith('do shell script "'))
        self.assertTrue(argv[2].endswith('" with administrator privileges'))
        self.assertIn("/usr/bin/python3 -I -S -c", argv[2])
        self.assertTrue(result["prepared"])

    def test_cli_source_and_output_boundaries_and_no_execute_flag(self):
        for args in (("--source", str(self.source), "--output", str(self.output)),
                     ("--output", str(self.output)),
                     ("--output", str(prepare.CANONICAL_SOURCE / "docs/evidence/x"), "--execute")):
            with patch.object(sys, "argv", ["prepare"] + list(args)), \
                    patch.object(prepare, "prepare") as callback, redirect_stdout(io.StringIO()), \
                    redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                prepare.main()
            callback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
