import unittest
from unittest.mock import MagicMock, patch

import npc_udp_physical_relay as relay


class PhysicalUDPPathTest(unittest.TestCase):
    def test_protocol_ports_and_fixed_destination(self):
        self.assertEqual(relay.DESTINATION_HOST, '146.56.249.175')
        self.assertEqual(relay.PORTS['quic'], (18025, 8025))
        self.assertEqual(relay.PORTS['kcp'], (18026, 8024))

    def test_rejects_tun_and_missing_interface(self):
        sock = MagicMock()
        with patch.object(relay.sys, 'platform', 'darwin'), \
             patch.object(relay.socket, 'socket', return_value=sock), \
             patch.object(relay.socket, 'if_nametoindex', side_effect=OSError):
            with self.assertRaises(OSError):
                relay.physical_udp_connect(('146.56.249.175', 8025),
                                           ('utun1024', 'en99'))
        sock.connect.assert_not_called()

    def test_binding_must_be_verified_before_udp_connect(self):
        sock = MagicMock()
        sock.getsockopt.return_value = 0
        with patch.object(relay.sys, 'platform', 'darwin'), \
             patch.object(relay.socket, 'socket', return_value=sock), \
             patch.object(relay.socket, 'if_nametoindex', return_value=29):
            with self.assertRaises(OSError):
                relay.physical_udp_connect(('146.56.249.175', 8025), ('en9',))
        sock.connect.assert_not_called()
        sock.close.assert_called_once()

    def test_source_address_must_belong_to_bound_active_nic(self):
        sock = MagicMock()
        sock.getsockopt.return_value = 29
        sock.getsockname.return_value = ('192.168.9.8', 54801)
        with patch.object(relay.sys, 'platform', 'darwin'), \
             patch.object(relay.socket, 'socket', return_value=sock), \
             patch.object(relay.socket, 'if_nametoindex', return_value=29), \
             patch.object(relay, 'interface_usable', return_value=False):
            with self.assertRaises(OSError):
                relay.physical_udp_connect(('146.56.249.175', 8025), ('en9',))
        sock.close.assert_called_once()

    def test_validated_physical_source_can_connect(self):
        sock = MagicMock()
        sock.getsockopt.return_value = 29
        sock.getsockname.return_value = ('192.168.9.8', 54801)
        with patch.object(relay.sys, 'platform', 'darwin'), \
             patch.object(relay.socket, 'socket', return_value=sock), \
             patch.object(relay.socket, 'if_nametoindex', return_value=29), \
             patch.object(relay, 'interface_usable', return_value=True):
            self.assertEqual(
                relay.physical_udp_connect(('146.56.249.175', 8025), ('en9',)),
                (sock, 'en9', 29, '192.168.9.8'))
        sock.connect.assert_called_once_with(('146.56.249.175', 8025))


if __name__ == '__main__':
    unittest.main()
