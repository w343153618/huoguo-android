import unittest
from unittest.mock import patch, MagicMock
import npc_physical_relay as relay

class TickEvent:
    def __init__(self, ticks):
        self.ticks = ticks
    def wait(self, delay):
        self.ticks -= 1
        return self.ticks < 0

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

    def test_link_check_requires_active_link_and_unchanged_ipv4(self):
        active='en11: flags=8863<UP,RUNNING>\n\tinet 192.168.9.99 netmask 0xfffffe00\n\tstatus: active\n'
        with patch.object(relay.socket,'if_nametoindex',return_value=13), patch.object(relay.subprocess,'run') as command:
            command.return_value.returncode=0
            command.return_value.stdout=active
            self.assertTrue(relay.interface_usable('en11',13,'192.168.9.99'))
            self.assertFalse(relay.interface_usable('en11',13,'192.168.9.126'))
            command.return_value.stdout=active.replace('status: active','status: inactive')
            self.assertFalse(relay.interface_usable('en11',13,'192.168.9.99'))
            command.assert_called_with(['/sbin/ifconfig','en11'],capture_output=True,text=True,timeout=2,check=False)

    def test_wifi_reconnects_to_stable_reachable_wired_link(self):
        upstream=MagicMock();upstream.getsockname.return_value=('192.168.9.126',57174)
        probe=MagicMock()
        with patch.object(relay,'interface_usable',return_value=True), patch.object(relay.time,'monotonic',side_effect=(0,16)), patch.object(relay,'physical_connect',return_value=(probe,'en11',13)) as connect:
            reason=relay.watch_connection(upstream,'en0',17,TickEvent(2),('en11','en0'))
        self.assertIn('en11',reason)
        connect.assert_called_once_with(('en11',))
        probe.close.assert_called_once()

    def test_wifi_stays_when_wired_probe_fails(self):
        upstream=MagicMock();upstream.getsockname.return_value=('192.168.9.126',57174)
        with patch.object(relay,'interface_usable',return_value=True), patch.object(relay.time,'monotonic',side_effect=(0,16,17)), patch.object(relay,'physical_connect',side_effect=OSError('No wired path')) as connect:
            reason=relay.watch_connection(upstream,'en0',17,TickEvent(3),('en11','en0'))
        self.assertIsNone(reason)
        connect.assert_called_once_with(('en11',))

    def test_active_connection_restarts_when_its_link_is_lost(self):
        upstream=MagicMock();upstream.getsockname.return_value=('192.168.9.99',58401)
        with patch.object(relay,'interface_usable',return_value=False), patch.object(relay,'physical_connect') as connect:
            reason=relay.watch_connection(upstream,'en11',13,TickEvent(1),('en11','en0'))
        self.assertIn('lost',reason)
        connect.assert_not_called()
