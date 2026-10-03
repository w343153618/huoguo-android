"""Offline fixed-loopback NPS worker and explicit M5 source checks.

Only public fixture keys, fake sockets/processes and temporary guest capability
bytes are used. No live runtime, ADB, phone, cloud or listening socket is touched.
The true GCM/replay reader is exercised against in-memory datagrams.
"""
import json
from pathlib import Path
import socket
import tempfile
from unittest.mock import Mock, patch
import unittest

from udp_lan_worker import LanMediaWorker
from tests import test_udp_lan_worker as legacy_worker_checks
from tests.test_udp_lan_worker import (FakeSocket, KEY, SESSION,
                                     TAG, bare_worker, config)
from udp_probe_protocol import CLIENT_NONCE, seal


def nps_config(**changes):
    return config(network_scope='nps_owner', node='m5', peer_host='146.56.249.175',
                  peer_port=15558, local_bind_host='127.0.0.1', local_bind_port=45965,
                  guest_serial='emulator-5554', guest_avd='phone17-root', **changes)


class NpsWorkerChecks(unittest.TestCase):
    def test_public_advertised_port_never_becomes_local_UDP_bind(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy_worker_checks.ConstructorChecks().fixture(directory)
            udp = FakeSocket()
            with patch('udp_lan_worker.socket.socket', return_value=udp), \
                 patch('udp_lan_worker.socket.if_nametoindex', return_value=1) as index, \
                 patch.object(LanMediaWorker, '_background') as background:
                worker = LanMediaWorker(nps_config(), '127.0.0.1', '127.0.0.1', 'lo0',
                    directory, '/fake/packetizer', '/fake/encoder', Mock(), directory, Mock(),
                    guest_serial='emulator-5554', guest_avd='phone17-root')
            index.assert_called_once_with('lo0')
            self.assertEqual(udp.options[socket.IPPROTO_IP, 25], 1)
            self.assertEqual(udp.binds, [('127.0.0.1', 45965)])
            self.assertNotIn(('146.56.249.175', 15558), udp.binds)
            self.assertEqual((worker.guest_serial, worker.guest_avd), ('emulator-5554', 'phone17-root'))
            background.assert_called_once()
            worker.stop()
            reports = list(Path(directory).glob('host-session-*.json'))
            self.assertEqual(len(reports), 1)
            report = json.loads(reports[0].read_text())
            self.assertEqual(report['source'], 'm5_emulator_5554')
            self.assertEqual(report['path'], 'NPS_public_UDP_task_AESGCM_UDP_inner_bridge_unverified')
            self.assertNotIn(nps_config()['key_b64'], reports[0].read_text())

    def test_mismatched_NPS_private_backend_or_guest_fails_before_socket(self):
        cases = [('127.0.0.2', '127.0.0.1', 'lo0', {}, {}),
                 ('127.0.0.1', '0.0.0.0', 'lo0', {}, {}),
                 ('127.0.0.1', '127.0.0.1', 'en7', {}, {}),
                 ('127.0.0.1', '127.0.0.1', 'lo0', {'local_bind_port': 15558}, {}),
                 ('127.0.0.1', '127.0.0.1', 'lo0', {'local_bind_port': True}, {}),
                 ('127.0.0.1', '127.0.0.1', 'lo0', {'local_bind_host': '127.0.0.2'}, {}),
                 ('127.0.0.1', '127.0.0.1', 'lo0', {'guest_serial': 'emulator-5556'}, {}),
                 ('127.0.0.1', '127.0.0.1', 'lo0', {}, {'guest_avd': 'RemoteAndroid17Compare'})]
        for peer, host, interface, config_changes, argument_changes in cases:
            settings = nps_config(); settings.update(config_changes)
            arguments = {'guest_serial': 'emulator-5554', 'guest_avd': 'phone17-root'}
            arguments.update(argument_changes)
            with self.subTest(host=host, interface=interface, changes=config_changes), \
                 patch('udp_lan_worker.socket.socket') as create, \
                 patch.object(LanMediaWorker, '_background') as background:
                with self.assertRaises(ValueError):
                    LanMediaWorker(settings, peer, host, interface, '/fake/runtime',
                        '/fake/packetizer', '/fake/encoder', Mock(), '/fake/evidence', Mock(),
                        **arguments)
                create.assert_not_called(); background.assert_not_called()

    def test_explicit_guest_is_bounded_emulator_and_safe_AVD_name(self):
        for serial, avd in (('phone-usb', 'phone17-root'), ('emulator-65536', 'phone17-root'),
                            ('emulator-05554', 'phone17-root'), (None, 'phone17-root'),
                            ('emulator-5554', 'phone17-root;id'), ('emulator-5554', ''),
                            ('emulator-5554', '../phone17-root'), ('emulator-5554', True)):
            with self.subTest(serial=serial, avd=avd), patch('udp_lan_worker.socket.socket') as create:
                with self.assertRaises(ValueError):
                    LanMediaWorker(config(), '192.168.9.149', '192.168.9.128', 'en7',
                        '/fake/runtime', '/fake/packetizer', '/fake/encoder', Mock(),
                        '/fake/evidence', Mock(), guest_serial=serial, guest_avd=avd)
                create.assert_not_called()

    def test_GCM_READY_only_pins_exact_NPC_loopback_tuple_and_cannot_be_taken_over(self):
        worker = bare_worker()
        worker.config = nps_config()
        worker.peer_ip, worker.host_ip = '127.0.0.1', '127.0.0.1'
        worker.guest_serial, worker.guest_avd = 'emulator-5554', 'phone17-root'
        worker.touch = Mock()
        def packet(seq, payload): return seal(KEY, TAG, seq, payload, CLIENT_NONCE)
        corrupt = bytearray(packet(999999, b'READY')); corrupt[-1] ^= 1
        fixed = ('127.0.0.1', 50007)
        other = ('127.0.0.1', 50008)
        worker.udp = FakeSocket([
            (packet(999999, b'READY'), ('127.0.0.2', 50006)),
            (bytes(corrupt), ('127.0.0.1', 50006)),
            (packet(1, b'ALIVE'), other),
            (packet(2, b'READY'), fixed),
            (packet(999999, b'ALIVE'), other),
            (packet(3, b'ALIVE'), fixed),
            (packet(3, b'ALIVE'), fixed),
            (packet(4, b'STOP'), fixed),
        ], worker.stop_event)
        with patch('udp_lan_worker.AuthenticatedSender', return_value=Mock()) as sender:
            worker._receive()
        self.assertEqual(worker.peer, fixed)
        self.assertEqual(worker.udp.connects, [fixed])
        self.assertEqual(worker.counts['foreign_peer'], 2)
        self.assertEqual(worker.counts['invalid_packets'], 2)
        worker.registry.authenticated_ready.assert_called_once_with(SESSION)
        worker.registry.authenticated_alive.assert_called_once_with(SESSION)
        worker.registry.revoke.assert_called_once_with(SESSION)
        sender.assert_called_once()

    def test_M5_native_and_touch_geometry_use_same_explicit_guest_not_M1_default(self):
        worker = bare_worker()
        worker.config = nps_config(touch_enabled=True)
        worker.guest_serial, worker.guest_avd = 'emulator-5554', 'phone17-root'
        worker.sender, worker._background = Mock(), Mock()
        hardware, native = Mock(), Mock()
        geometry = {'effective_width': 720, 'effective_height': 1280}
        with patch('udp_lan_worker.HostHardwareSession', return_value=hardware) as create, \
             patch('udp_lan_worker.subprocess.Popen', return_value=native), \
             patch('run_phone_udp.read_touch_geometry', return_value=geometry) as read, \
             patch('udp_lan_worker.UdpTouchBridge') as touch:
            worker._start_media()
        self.assertEqual(create.call_args.args[1:3], ('emulator-5554', 'phone17-root'))
        self.assertEqual(create.call_args.kwargs['raw_queue_policy'], 'fifo')
        self.assertEqual(create.call_args.kwargs['raw_submit_fps'], 60)
        self.assertEqual(read.call_args.kwargs, {'source_serial': 'emulator-5554'})
        self.assertEqual(touch.call_args.args[:2], (720, 1280))
        hardware.start.assert_called_once()


if __name__ == '__main__':
    unittest.main()
