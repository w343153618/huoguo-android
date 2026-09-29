"""Sleep the guest display after the last stream, without stopping the Android VM."""
import threading

class IdleScreen:
    def __init__(self, lock, has_sessions, sleep, delay=300, timer_factory=threading.Timer):
        self.lock, self.has_sessions, self.sleep = lock, has_sessions, sleep
        self.delay, self.timer_factory = delay, timer_factory
        self.timer = None
        self.generation = 0
        self.closed = False

    def cancel(self):
        with self.lock:
            self.generation += 1
            if self.timer is not None:
                self.timer.cancel()
                self.timer = None

    def schedule(self):
        with self.lock:
            if self.closed or self.has_sessions():
                return
            self.cancel()
            generation = self.generation
            def expired():
                # The same lock protects session startup: an old callback cannot
                # switch off the display while a new connection wakes it.
                with self.lock:
                    if self.closed or generation != self.generation or self.has_sessions():
                        return
                    self.timer = None
                    try:
                        self.sleep()
                    except Exception as error:
                        print('Idle display sleep deferred: '+type(error).__name__, flush=True)
                        self.schedule()
            self.timer = self.timer_factory(self.delay, expired)
            self.timer.daemon = True
            self.timer.start()

    def shutdown(self):
        with self.lock:
            self.closed = True
            self.cancel()
