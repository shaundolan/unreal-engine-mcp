"""HTTP client for the local text/image -> 3D server in servers/gen3d (stdlib only).

The server can run on another machine (e.g. a Windows RTX 4090 box): set UNREAL_MCP_GEN3D_URL=http://<host>:8765
and UNREAL_MCP_GEN3D_TOKEN to its --token.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any


class Gen3DError(RuntimeError):
    pass


class Gen3DClient:
    def __init__(self, url: str, token: str | None = None, timeout_s: float = 30.0):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout_s = timeout_s

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None, raw: bool = False) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                payload = r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            try:
                detail = json.loads(detail).get("detail", detail)
            except (ValueError, AttributeError):
                pass
            raise Gen3DError(f"gen3d server {method} {path}: HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, OSError) as e:
            raise Gen3DError(f"gen3d server not reachable at {self.url} ({getattr(e, 'reason', e)}); start "
                             "servers/gen3d/gen3d_server.py or set UNREAL_MCP_GEN3D_URL") from e
        return payload if raw else json.loads(payload)

    def info(self) -> dict[str, Any]:
        return self._request("GET", "/info")

    def submit(self, prompt: str = "", image_path: str = "", quality: str = "standard", seed: int = 0,
               params: dict[str, Any] | None = None, **extra: Any) -> str:
        body: dict[str, Any] = {"quality": quality, "seed": seed, "params": params or {}, **extra}
        if prompt:
            body["prompt"] = prompt
        if image_path:
            p = Path(image_path).expanduser()
            if not p.is_file():
                raise Gen3DError(f"image {p} not found on the MCP server machine")
            body["image_b64"] = base64.b64encode(p.read_bytes()).decode()
            body["image_name"] = p.name
        return self._request("POST", "/generate", body)["job_id"]

    def job(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/jobs/{job_id}")

    def wait(self, job_id: str, timeout_s: float = 900.0, poll_s: float = 1.0,
             sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        while True:
            j = self.job(job_id)
            if j["status"] in ("done", "error", "cancelled"):
                return j
            if time.monotonic() > deadline:
                raise Gen3DError(f"job {job_id} still {j['status']} ({j.get('stage')}) after {timeout_s}s; "
                                 "it keeps running on the server: poll it again with gen3d_status(job_id=...)")
            sleep(poll_s)

    def download(self, job_id: str, dest: Path, what: str = "result") -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self._request("GET", f"/jobs/{job_id}/{what}", raw=True))
        return dest
