"""Per-data-directory process lease. Reopening an EXE reopens its existing local page."""
import json
import os
import time
from pathlib import Path

class ServiceLease:
    def __init__(self, data):
        self.path = Path(data) / 'service-instance.json'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(self.path, 'a+b')
        if not self.path.stat().st_size:
            self.file.write(b' '); self.file.flush()
        self.file.seek(0)
        self.owned = False
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.owned = True
            self.publish(None)
        except (OSError, BlockingIOError):
            pass

    def publish(self, port):
        if not self.owned: raise RuntimeError('Lease not owned')
        self.file.seek(1); self.file.truncate()
        self.file.write(json.dumps({'pid': os.getpid(), 'port': port}).encode('ascii'))
        self.file.flush()

    def existing_port(self, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                self.file.seek(1)
                port = json.loads(self.file.read().decode('ascii'))['port']
                if isinstance(port, int) and 1024 <= port <= 65535: return port
            except (OSError, ValueError, KeyError): pass
            time.sleep(.2)
        return None

    def close(self):
        if self.file.closed: return
        if self.owned:
            self.file.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        self.file.close()
