"""Candidate-generator checks and inert Darwin child probes; never run a VM.

Every live socket belongs to these fixtures, and every readable/writable canary
is freshly created by the test. These checks do not prove emulator, GPU, guest
root, DNS/PF, proxy, management-interface or production isolation acceptance.
"""
from dataclasses import replace
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

from scripts.security.emulator_sandbox_profile import (
    FORBIDDEN_ROOTS, ISOLATION_BASE, SandboxConfig, check_compiles,
    generate_profile, sbpl_string,
)


class SandboxProfileTest(unittest.TestCase):
    def setUp(self):
        self.config = SandboxConfig(proxy_tcp_port=28970)

    def rejects(self, **kwargs):
        with self.assertRaises(ValueError):
            generate_profile(replace(self.config, **kwargs))

    def test_only_one_concrete_outbound_tcp_endpoint_and_no_shared_adb(self):
        profile = generate_profile(self.config)
        self.assertEqual(profile.count('(remote tcp "localhost:28970")'), 1)
        self.assertNotIn('(remote tcp "localhost:*")', profile)
        self.assertNotIn('(remote tcp "*:', profile)
        self.assertNotIn('(remote udp ', profile)
        self.assertNotIn(':5037', profile)
        self.assertIn('(deny network-bind)', profile)
        self.assertIn('(deny network-inbound)', profile)

    def test_management_inbound_never_adds_an_outbound_exception(self):
        profile = generate_profile(replace(self.config, listen_tcp_ports=(15591, 18590)))
        self.assertEqual(profile.count('(remote tcp '), 1)
        self.assertEqual(profile.count('(local tcp "localhost:15591")'), 2)
        self.assertEqual(profile.count('(local tcp "localhost:18590")'), 2)
        self.rejects(listen_tcp_ports=(28970,))
        self.rejects(listen_tcp_ports=(5037,))
        self.rejects(listen_tcp_ports=(15591, 15591))

    def test_dns_is_explicit_localhost_port_limited_and_never_public(self):
        profile = generate_profile(replace(self.config, dns_loopback_port=53))
        self.assertEqual(profile.count('(remote udp "localhost:53")'), 1)
        self.assertNotIn('(remote udp "*:53")', profile)
        self.assertNotIn('(remote udp "*:*"', profile)
        self.assertIn('no public UDP53', profile)
        for value in (0, True, '53', 65536):
            self.rejects(dns_loopback_port=value)

    def test_personal_mounts_aliases_and_automation_are_explicitly_denied(self):
        profile = generate_profile(self.config)
        denial = profile[profile.index('(deny file-read* file-write* file-map-executable'):]
        for path in FORBIDDEN_ROOTS:
            self.assertIn('(subpath ' + sbpl_string(path) + ')', denial)
        self.assertNotIn('(subpath "/System")', profile)
        self.assertNotIn('(subpath "/")', profile)
        self.assertNotIn('(allow file-read', profile)
        self.assertIn('(deny appleevent-send)', profile)
        self.assertIn('(deny mach-lookup', profile)
        self.assertIn('com.apple.SecurityServer', profile)

    def test_paths_cannot_reference_personal_home_or_escape_by_parent_components(self):
        for value in ('/Users/fixture/sdk', '/Volumes/fixture/sdk', '/Network/sdk',
                      'relative-sdk', str(ISOLATION_BASE),
                      str(ISOLATION_BASE) + '/guest/../sdk',
                      str(ISOLATION_BASE) + '/./sdk',
                      str(ISOLATION_BASE) + '-other/sdk'):
            for field in ('runtime_root', 'sdk_root', 'avd_root'):
                self.rejects(**{field: Path(value) if '/./' not in value else value})
        self.rejects(immutable_code_roots=(Path('/Users/fixture/code'),))

    def test_write_scope_never_contains_immutable_sdk_or_code(self):
        self.rejects(runtime_root=ISOLATION_BASE / 'sdk')
        self.rejects(sdk_root=ISOLATION_BASE / 'guest/sdk')
        self.rejects(avd_root=ISOLATION_BASE / 'sdk/avd')
        self.rejects(immutable_code_roots=(ISOLATION_BASE / 'guest/code',))
        profile = generate_profile(replace(self.config, immutable_code_roots=(ISOLATION_BASE / 'code',)))
        write_clause = profile[profile.index('(deny file-write*'):profile.index('(deny file-map-executable')]
        self.assertNotIn('(subpath "' + str(ISOLATION_BASE / 'sdk') + '")', write_clause)
        self.assertNotIn('(subpath "' + str(ISOLATION_BASE / 'code') + '")', write_clause)
        self.assertIn('(subpath "' + str(ISOLATION_BASE / 'guest') + '")', write_clause)

    def test_homebrew_is_opt_in_canonical_package_version_not_global_prefix(self):
        for value in ('/opt/homebrew', '/opt/homebrew/Cellar',
                      '/opt/homebrew/Cellar/fictional', '/opt/homebrew/opt/fictional',
                      '/usr/local/lib', '/Library/Frameworks'):
            self.rejects(homebrew_read_roots=(Path(value),))
        path = Path('/opt/homebrew/Cellar/huoguo-fixture/0.0-fixture/lib')
        profile = generate_profile(replace(self.config, homebrew_read_roots=(path,)))
        self.assertIn('(subpath "' + str(path) + '")', profile)
        self.assertNotIn('(subpath "/opt/homebrew")', profile)

    def test_existing_symlink_parent_is_not_accepted_as_immutable_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / 'target'
            target.mkdir()
            alias = root / 'alias'
            alias.symlink_to(target, target_is_directory=True)
            # _path is not itself the security decision; canonical checking in
            # the generator must reject aliases rather than resolve and widen.
            from scripts.security.emulator_sandbox_profile import _isolated_path
            from unittest.mock import patch
            with patch('scripts.security.emulator_sandbox_profile.ISOLATION_BASE', root):
                with self.assertRaisesRegex(ValueError, 'symlink-free'):
                    _isolated_path(alias / 'not-yet-created')

    def test_sbpl_quoting_cannot_inject_an_allow_or_new_operation(self):
        attack = 'sdk")) (allow network-outbound) ;\\quoted'
        quoted = sbpl_string(attack)
        self.assertTrue(quoted.startswith('"') and quoted.endswith('"'))
        self.assertIn('\\"))', quoted)
        self.assertIn('\\\\quoted', quoted)
        profile = generate_profile(replace(self.config, sdk_root=ISOLATION_BASE / attack))
        self.assertNotIn('(subpath "' + str(ISOLATION_BASE) + '/sdk"))', profile)
        for unsafe in ('value\n(allow default)', 'nul\0byte', 'del\x7fbyte'):
            with self.assertRaises(ValueError):
                sbpl_string(unsafe)

    def test_port_ranges_types_and_wildcards_fail_closed(self):
        for value in (0, 53, 1023, 65536, -1, True, '28970', '*', 28970.0):
            self.rejects(proxy_tcp_port=value)

    def test_candidate_warning_is_never_a_supported_sandbox_claim(self):
        profile = generate_profile(self.config)
        self.assertIn('EXPERIMENTAL ONLY', profile)
        self.assertIn('deprecated sandbox-exec', profile)
        self.assertIn('Other default-allowed Mach/IOKit/process capabilities remain unaccepted', profile)


@unittest.skipUnless(sys.platform == 'darwin' and Path('/usr/bin/sandbox-exec').is_file(),
                     'Darwin-only inert child probes')
class DarwinSyntheticSandboxTest(unittest.TestCase):
    def child(self, profile, command, payload=b''):
        return subprocess.run(['/usr/bin/sandbox-exec', '-p', profile, *command],
                              cwd='/', input=payload, capture_output=True, timeout=5)

    def test_default_and_explicit_options_compile_without_launching_emulator(self):
        configs = (SandboxConfig(proxy_tcp_port=28970),
                   SandboxConfig(proxy_tcp_port=28970, listen_tcp_ports=(15591, 18590),
                                 dns_loopback_port=53))
        for config in configs:
            result = check_compiles(generate_profile(config))
            self.assertTrue(result['compiled'], result)
            self.assertIn('no emulator/GPU/Hypervisor acceptance', result['scope'])

    def test_injection_shaped_quoted_path_is_still_valid_profile_data(self):
        profile = generate_profile(SandboxConfig(proxy_tcp_port=28970,
            sdk_root=ISOLATION_BASE / 'fixture")) (allow network-outbound) ;\\x'))
        self.assertTrue(check_compiles(profile)['compiled'])

    def test_fresh_outside_file_cannot_be_read_changed_or_created(self):
        profile = generate_profile(SandboxConfig(proxy_tcp_port=28970))
        with tempfile.TemporaryDirectory() as directory:
            existing = Path(directory) / 'owned-canary'
            new = Path(directory) / 'owned-new'
            existing.write_bytes(b'fresh nonsensitive fixture only')
            read = self.child(profile, ['/bin/cat', str(existing)])
            self.assertNotEqual(read.returncode, 0)
            self.assertEqual(read.stdout, b'')
            for target in (existing, new):
                write = self.child(profile, ['/bin/sh', '-c', 'printf changed > "$1"', 'fixture', str(target)])
                self.assertNotEqual(write.returncode, 0)
            self.assertEqual(existing.read_bytes(), b'fresh nonsensitive fixture only')
            self.assertFalse(new.exists())
        # No personal data is opened; this is a public immutable OS executable.
        permitted = self.child(profile, ['/bin/cat', '/usr/bin/true'])
        self.assertEqual(permitted.returncode, 0)
        self.assertGreater(len(permitted.stdout), 0)

    def test_proxy_endpoint_allowed_other_owned_loopback_endpoint_denied(self):
        allowed, other = socket.socket(), socket.socket()
        self.addCleanup(allowed.close)
        self.addCleanup(other.close)
        for server in (allowed, other):
            server.bind(('127.0.0.1', 0))
            server.listen(1)
            server.settimeout(2)
        profile = generate_profile(SandboxConfig(proxy_tcp_port=allowed.getsockname()[1]))
        accepted = threading.Event()
        failure = []

        def send_owned_marker():
            try:
                client, _ = allowed.accept()
                with client:
                    client.sendall(b'fresh-owned-proxy-fixture\n')
                accepted.set()
            except Exception as error:
                failure.append(str(error))

        worker = threading.Thread(target=send_owned_marker, daemon=True)
        worker.start()
        result = self.child(profile, ['/usr/bin/nc', '-w', '1', '127.0.0.1', str(allowed.getsockname()[1])])
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failure, [])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(accepted.is_set())
        self.assertIn(b'fresh-owned-proxy-fixture', result.stdout)
        rejected = self.child(profile, ['/usr/bin/nc', '-w', '1', '127.0.0.1', str(other.getsockname()[1])])
        self.assertNotEqual(rejected.returncode, 0)
        other.settimeout(0.05)
        with self.assertRaises(socket.timeout):
            other.accept()

    def test_only_designated_owned_dns_udp_endpoint_can_send_and_receive(self):
        allowed, other = socket.socket(type=socket.SOCK_DGRAM), socket.socket(type=socket.SOCK_DGRAM)
        self.addCleanup(allowed.close)
        self.addCleanup(other.close)
        for server in (allowed, other):
            server.bind(('127.0.0.1', 0))
            server.settimeout(2)
        profile = generate_profile(SandboxConfig(proxy_tcp_port=18131,
            dns_loopback_port=allowed.getsockname()[1]))
        seen, failure = [], []

        def reply_owned_marker():
            try:
                data, address = allowed.recvfrom(128)
                seen.append(data)
                allowed.sendto(b'owned-dns-guard-fixture', address)
            except Exception as error:
                failure.append(str(error))

        worker = threading.Thread(target=reply_owned_marker, daemon=True)
        worker.start()
        payload = b'fresh-owned-udp-request'
        received = self.child(profile, ['/usr/bin/nc', '-u', '-w', '1', '127.0.0.1',
                                       str(allowed.getsockname()[1])], payload)
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failure, [])
        self.assertEqual(seen, [payload])
        self.assertEqual(received.returncode, 0, received.stderr)
        self.assertIn(b'owned-dns-guard-fixture', received.stdout)
        rejected = self.child(profile, ['/usr/bin/nc', '-u', '-w', '1', '127.0.0.1',
                                       str(other.getsockname()[1])], payload)
        self.assertNotEqual(rejected.returncode, 0)
        other.settimeout(0.05)
        with self.assertRaises(socket.timeout):
            other.recvfrom(128)


if __name__ == '__main__':
    unittest.main()
