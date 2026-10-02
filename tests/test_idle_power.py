import threading
import unittest
from idle_power import IdleScreen

class Timer:
    def __init__(self, delay, callback):
        self.delay, self.callback = delay, callback
        self.cancelled = False
    def start(self): pass
    def cancel(self): self.cancelled = True

class IdlePowerTest(unittest.TestCase):
    def setUp(self):
        self.sessions = []
        self.sleeps = []
        self.power = IdleScreen(threading.RLock(), lambda: bool(self.sessions), lambda: self.sleeps.append('sleep'), timer_factory=Timer)
    def test_five_minutes_idle_then_sleep(self):
        self.power.schedule()
        self.assertEqual(self.power.timer.delay, 300)
        self.power.timer.callback()
        self.assertEqual(self.sleeps, ['sleep'])
    def test_zero_delay_keeps_guest_awake_at_startup_and_after_disconnect(self):
        def forbidden_timer(*args):
            raise AssertionError('Disabled idle sleep must not create a timer')
        power = IdleScreen(threading.RLock(), lambda: bool(self.sessions),
                           lambda: self.sleeps.append('sleep'), delay=0,
                           timer_factory=forbidden_timer)
        power.schedule()  # Gateway startup with no sessions.
        self.sessions.append('active'); power.schedule()
        self.sessions.clear(); power.schedule()  # Last session disconnected.
        power.schedule(); power.shutdown(); power.schedule()
        self.assertIsNone(power.timer)
        self.assertEqual(self.sleeps, [])
    def test_disabling_cancels_queued_callback_even_during_active_session(self):
        self.power.schedule(); stale = self.power.timer
        self.sessions.append('active')
        self.power.delay = 0; self.power.schedule()
        self.assertTrue(stale.cancelled)
        self.assertIsNone(self.power.timer)
        self.sessions.clear()
        stale.callback()  # Timer.cancel() cannot remove an already queued callback.
        self.power.schedule()
        self.assertIsNone(self.power.timer)
        self.assertEqual(self.sleeps, [])
    def test_negative_delay_is_rejected(self):
        with self.assertRaises(ValueError):
            IdleScreen(threading.RLock(), lambda: False, lambda: None, delay=-1)
    def test_reconnect_cancels_stale_timer_even_if_already_queued(self):
        self.power.schedule(); stale = self.power.timer
        self.power.cancel(); self.sessions.append('new')
        stale.callback()
        self.assertTrue(stale.cancelled)
        self.assertEqual(self.sleeps, [])
    def test_existing_connection_prevents_sleep(self):
        self.sessions.append('active'); self.power.schedule()
        self.assertIsNone(self.power.timer)
        self.sessions.clear(); self.power.schedule(); timer = self.power.timer
        self.sessions.append('active'); timer.callback()
        self.assertEqual(self.sleeps, [])
    def test_repeated_disconnect_restarts_timer_and_shutdown_cancels(self):
        self.power.schedule(); old = self.power.timer
        self.power.schedule(); current = self.power.timer
        old.callback(); self.assertEqual(self.sleeps, [])
        self.power.shutdown(); current.callback(); self.power.schedule()
        self.assertEqual(self.sleeps, []); self.assertIsNone(self.power.timer)
    def test_unavailable_guest_retries_without_waking_active_session(self):
        def unavailable(): raise OSError('offline')
        self.power.sleep = unavailable
        self.power.schedule(); self.power.timer.callback()
        self.assertEqual(self.power.timer.delay, 300)
        self.power.cancel(); self.sessions.append('new')
        self.assertEqual(self.sleeps, [])
