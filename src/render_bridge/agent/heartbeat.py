"""Worker liveness reporting only; never schedules or retries a rendering task."""
import threading


class HeartbeatLoop:
    def __init__(self, node, interval=30):
        if not 10 <= interval <= 60:
            raise ValueError('Heartbeat interval must be between 10 and 60 seconds')
        self.node, self.interval = node, interval
        self.stop = threading.Event()
        self.thread = None

    def __enter__(self):
        self.node._worker_running = True
        self.node.heartbeat()
        self.thread = threading.Thread(target=self._run, name='bridge-heartbeat', daemon=True)
        self.thread.start()
        return self

    def _run(self):
        while not self.stop.wait(self.interval):
            self.node.heartbeat()

    def __exit__(self, *_exc):
        self.node._worker_running = False
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=1)
        self.node.heartbeat()
