import unittest
from unittest.mock import patch, MagicMock
import npc_physical_relay as relay

class PhysicalPathTest(unittest.TestCase):
    def test_tunnel_and_missing_interface_do_not_connect(self):
        sock=MagicMock()
        with patch.object(relay.sys,'platform','darwin'),patch.object(relay.socket,'socket',return_value=sock),patch.object(relay.socket,'if_nametoindex',side_effect=OSError):
            with self.assertRaises(OSError):relay.physical_connect(('utun5','en99'))
        sock.connect.assert_not_called()
        sock.close.assert_called_once()
    def test_binding_failure_does_not_fall_back_to_unbound_socket(self):
        sock=MagicMock();sock.setsockopt.side_effect=OSError('Binding denied')
        with patch.object(relay.sys,'platform','darwin'),patch.object(relay.socket,'socket',return_value=sock),patch.object(relay.socket,'if_nametoindex',return_value=13):
            with self.assertRaises(OSError):relay.physical_connect(('en11',))
        sock.connect.assert_not_called()
    def test_verified_binding_before_fixed_destination_connect(self):
        sock=MagicMock();sock.getsockopt.return_value=13
        with patch.object(relay.sys,'platform','darwin'),patch.object(relay.socket,'socket',return_value=sock),patch.object(relay.socket,'if_nametoindex',return_value=13):
            self.assertEqual(relay.physical_connect(('en11',)),(sock,'en11',13))
        sock.connect.assert_called_once_with(('146.56.249.175',8024))
