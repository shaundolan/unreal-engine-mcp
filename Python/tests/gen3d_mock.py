"""Run the real servers/gen3d FastAPI app with the mock backend in a background thread (for tests)."""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[2] / "servers" / "gen3d"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MockGen3D:
    def __init__(self, token: str | None = None, backend: str = "mock", t2i: str = "none"):
        import uvicorn
        from gen3d_server import Service, create_app
        self.service = Service(backend, t2i, token)
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.server = uvicorn.Server(uvicorn.Config(create_app(self.service), host="127.0.0.1", port=self.port,
                                                    log_level="warning", lifespan="off"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("mock gen3d server did not start")
            time.sleep(0.02)

    def close(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)
