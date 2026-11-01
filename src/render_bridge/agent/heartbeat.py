"""Worker liveness reporting only; retries liveness and never schedules rendering."""
import threading


class HeartbeatLoop:
    def __init__(self, node, interval=30):
        if not 10 <= interval <= 60:
            raise ValueError('Heartbeat interval must be between 10 and 60 seconds')
        self.node, self.interval = node, interval
        self.retry_interval = 5
        self.stop = threading.Event()
        self.thread = None

    def _next_interval(self, heartbeat_ok):
        return self.interval if heartbeat_ok is not False else self.retry_interval

    def __enter__(self):
        self.node._worker_running = True
        first_heartbeat_ok = self.node.heartbeat()
        self.thread = threading.Thread(
            target=self._run,
            args=(self._next_interval(first_heartbeat_ok),),
            name='bridge-heartbeat',
            daemon=True,
        )
        self.thread.start()
        return self

    def _run(self, next_interval=None):
        delay = self.interval if next_interval is None else next_interval
        while not self.stop.wait(delay):
            delay = self._next_interval(self.node.heartbeat())

    def __exit__(self, *_exc):
        self.node._worker_running = False
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=1)
        self.node.heartbeat()
