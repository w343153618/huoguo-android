import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import run_formal_quic_relay as formal_relay
from scripts import run_m1_npc as npc


class FormalNPCExecutionTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.config = Path(self.folder.name)/'identity.conf'
        self.environment = {'M1_NPC_CONFIG': str(self.config),
                            'M1_NPC_BINARY': '/private/example/m5/npc',
                            'NPC_CONFIG_PATH': '/must/not/override.conf',
                            'NPC_SERVER_ADDR': 'untrusted.example:1',
                            'NPC_SERVER_VKEY': 'must-be-replaced'}

    def tearDown(self):
        self.folder.cleanup()

    def identity(self, address, protocol, key='fixture-not-a-real-key'):
        self.config.write_text('[common]\nserver_addr='+address+'\nconn_type='+protocol+'\nvkey='+key+'\n')
        self.config.chmod(0o600)

    def test_original_tcp_rollback_and_quic_are_explicit_routes(self):
        for address, protocol in [('127.0.0.1:18024', 'tcp'), ('127.0.0.1:48126', 'quic')]:
            with self.subTest(protocol=protocol):
                self.identity(address, protocol)
                binary, arguments, environment = npc.execution(self.environment)
                self.assertEqual(binary, Path('/private/example/m5/npc'))
                self.assertEqual(arguments[1], '-type='+protocol)
                self.assertEqual(environment['NPC_SERVER_ADDR'], address)
                self.assertEqual(environment['NPC_SERVER_VKEY'], 'fixture-not-a-real-key')
                self.assertNotIn('NPC_CONFIG_PATH', environment)
                self.assertFalse(any('fixture-not-a-real-key' in value for value in arguments))
                self.assertEqual(self.environment['NPC_SERVER_VKEY'], 'must-be-replaced')

    def test_wrong_route_type_and_nonloopback_are_rejected(self):
        for address, protocol in [('127.0.0.1:18024', 'quic'), ('127.0.0.1:48126', 'tcp'),
                                  ('127.0.0.1:48026', 'quic'), ('146.56.249.175:8025', 'quic'),
                                  ('localhost:48126', 'quic'), ('127.0.0.1:48126', 'kcp')]:
            with self.subTest(address=address, protocol=protocol):
                self.identity(address, protocol)
                with self.assertRaises(SystemExit):
                    npc.execution(self.environment)

    def test_identity_requires_regular_0600_file(self):
        self.identity('127.0.0.1:48126', 'quic')
        for mode in (0o644, 0o640, 0o660, 0o400):
            with self.subTest(mode=mode):
                self.config.chmod(mode)
                with self.assertRaises(SystemExit):
                    npc.execution(self.environment)

    def test_empty_key_rejected_and_literal_percent_key_not_interpolated(self):
        self.identity('127.0.0.1:48126', 'quic', '')
        with self.assertRaises(SystemExit):
            npc.execution(self.environment)
        self.identity('127.0.0.1:48126', 'quic', 'fixture%literal#key')
        self.assertEqual(npc.execution(self.environment)[2]['NPC_SERVER_VKEY'], 'fixture%literal#key')

    def test_invalid_identity_does_not_echo_sensitive_parser_line(self):
        self.config.write_text('[common]\nserver_addr=127.0.0.1:48126\nconn_type=quic\n'
                               'vkey=fixture-sensitive-value\nvkey=fixture-other-sensitive-value\n')
        self.config.chmod(0o600)
        with self.assertRaises(SystemExit) as failure:
            npc.execution(self.environment)
        self.assertEqual(str(failure.exception), 'NPC identity configuration is invalid')

    def test_main_executes_secret_only_in_environment(self):
        self.identity('127.0.0.1:48126', 'quic')
        with patch.dict(os.environ, self.environment, clear=True), patch.object(npc.os, 'execve') as execute:
            npc.main()
        execute.assert_called_once()
        binary, arguments, environment = execute.call_args.args
        self.assertEqual(binary, Path('/private/example/m5/npc'))
        self.assertEqual(arguments[1], '-type=quic')
        self.assertEqual(environment['NPC_SERVER_VKEY'], 'fixture-not-a-real-key')
        self.assertNotIn('fixture-not-a-real-key', arguments)


class FormalQuicRelayEntryTest(unittest.TestCase):
    def test_physical_interfaces_must_be_explicit_and_ordered(self):
        self.assertEqual(formal_relay.physical_interfaces({'NPC_PHYSICAL_INTERFACES': 'en7,en0'}), ('en7', 'en0'))
        self.assertEqual(formal_relay.physical_interfaces({'NPC_PHYSICAL_INTERFACES': 'en5, en0'}), ('en5', 'en0'))
        for value in ('', 'utun0', 'en7,utun0', 'en7,', 'en7,en7', 'lo0'):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                formal_relay.physical_interfaces({'NPC_PHYSICAL_INTERFACES': value})

    def test_entry_only_uses_fixed_formal_quic_ports(self):
        original = dict(formal_relay.relay.PORTS)
        calls = []
        def serve(protocol, interfaces):
            calls.append((protocol, interfaces, dict(formal_relay.relay.PORTS), formal_relay.relay.DESTINATION_HOST))
        with patch.dict(os.environ, {'NPC_PHYSICAL_INTERFACES': 'en7,en0'}, clear=True), \
             patch.object(formal_relay.relay, 'PORTS', original), \
             patch.object(formal_relay.relay, 'serve', side_effect=serve):
            formal_relay.main([])
        self.assertEqual(calls, [('quic', ('en7', 'en0'), {'quic': (48126, 8025)}, '146.56.249.175')])
        self.assertEqual(formal_relay.relay.PORTS, original)

    def test_missing_interface_refuses_before_core_ports_change_or_bind(self):
        original = dict(formal_relay.relay.PORTS)
        with patch.dict(os.environ, {}, clear=True), patch.object(formal_relay.relay, 'serve') as serve:
            with self.assertRaises(SystemExit):
                formal_relay.main([])
        serve.assert_not_called()
        self.assertEqual(formal_relay.relay.PORTS, original)


if __name__ == '__main__':
    unittest.main()
