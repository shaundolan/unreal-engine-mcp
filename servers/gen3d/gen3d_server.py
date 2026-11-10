"""Local text/image -> 3D generation server for UnrealMCP's mesh_generate tool.

    python gen3d_server.py --backend triposr --t2i flux-schnell --host 0.0.0.0 --port 8765 [--token SECRET]

API (JSON; send `Authorization: Bearer <token>` when the server has a token):
    GET  /info                 backends, licences, VRAM, queue
    POST /generate             {prompt | image_b64, quality, seed, params} -> 202 {job_id}
    GET  /jobs/{id}            {status: queued|running|done|error, progress, stage, error, result}
    GET  /jobs/{id}/result     the GLB (model/gltf-binary)
    GET  /jobs/{id}/image      the conditioning image (PNG): the text->image output, or the input
    DELETE /jobs/{id}

One worker thread owns the GPU, so jobs run one at a time in submission order. Models load on the first job
(or at start with --preload). Only stdlib + fastapi + uvicorn + pillow are needed for --backend mock.
"""

from __future__ import annotations

import argparse
import base64
import io
import os
import queue
import secrets
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import JSONResponse, Response  # noqa: E402

import backends as B  # noqa: E402

MAX_JOBS_KEPT = 50
MAX_IMAGE_BYTES = 32 * 1024 * 1024


class Job:
    def __init__(self, req: dict[str, Any]):
        self.id = uuid.uuid4().hex[:12]
        self.req = req
        self.status, self.progress, self.stage, self.error = "queued", 0.0, "queued", None
        self.created, self.started, self.finished = time.time(), None, None
        self.glb: bytes | None = None
        self.image_png: bytes | None = None
        self.result: dict[str, Any] = {}

    def view(self) -> dict[str, Any]:
        now = time.time()
        return {"job_id": self.id, "status": self.status, "progress": round(self.progress, 3), "stage": self.stage,
                "error": self.error, "elapsed_s": round((self.finished or now) - (self.started or now), 2),
                "queued_s": round((self.started or now) - self.created, 2), "result": self.result or None}


class Service:
    def __init__(self, backend: str, t2i: str, token: str | None = None):
        if backend == "mock" and t2i == "none":
            t2i = "mock"  # text prompts work against the mock too
        if backend not in B.BACKENDS:
            raise SystemExit(f"unknown backend {backend!r}; one of {sorted(B.BACKENDS)}")
        if t2i not in B.TEXT_TO_IMAGE:
            raise SystemExit(f"unknown text-to-image {t2i!r}; one of {sorted(B.TEXT_TO_IMAGE)}")
        self.backend, self.t2i = B.BACKENDS[backend](), B.TEXT_TO_IMAGE[t2i]()
        self.token = token
        self.jobs: dict[str, Job] = {}
        self.q: queue.Queue[Job] = queue.Queue()
        self.loaded = {"backend": False, "t2i": False}
        self.lock = threading.Lock()
        threading.Thread(target=self._worker, daemon=True, name="gen3d-worker").start()

    # ---- worker ------------------------------------------------------------------------------------------
    def _ensure_loaded(self, which: str, job: Job | None = None) -> None:
        if self.loaded[which]:
            return
        if job:
            job.stage = f"loading {which} model"
        (self.backend if which == "backend" else self.t2i).load()
        self.loaded[which] = True

    def preload(self, t2i: bool) -> None:
        self._ensure_loaded("backend")
        if t2i and self.t2i.info.name not in ("none",):
            self._ensure_loaded("t2i")

    def _worker(self) -> None:
        while True:
            job = self.q.get()
            if job.status == "cancelled":
                continue
            job.status, job.started = "running", time.time()
            try:
                self._run(job)
                job.status, job.progress, job.stage = "done", 1.0, "done"
            except Exception as e:  # report, keep serving
                job.status, job.stage = "error", "error"
                job.error = f"{type(e).__name__}: {e}"
                job.result = {"traceback": traceback.format_exc()[-4000:]}
            finally:
                job.finished = time.time()
                self._gc_cuda()

    @staticmethod
    def _gc_cuda() -> None:
        torch = sys.modules.get("torch")
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _run(self, job: Job) -> None:
        req = job.req
        quality = req.get("quality", "standard")
        seed = int(req.get("seed", 0) or 0)
        params = dict(req.get("params") or {})
        params.setdefault("remove_background", req.get("remove_background", True))
        params.setdefault("texture", req.get("texture", True))
        warnings = list(self.backend.info.licence.warnings)
        if req.get("image_b64"):
            image = base64.b64decode(req["image_b64"])
            params["_hint"] = f"{req.get('image_name', '')} {req.get('prompt', '')}"
            source = "image"
        else:
            if self.t2i.info.name == "none":
                raise RuntimeError("this server has no text-to-image model (--t2i none): send an image")
            self._ensure_loaded("t2i", job)
            job.stage, job.progress = "text to image", 0.05
            kind = req.get("subject_kind") or "object"
            prompt = req["prompt"] + B.PROMPT_SUFFIX.format(kind=kind)
            image = self.t2i.generate(prompt, seed, quality)
            params["_hint"] = req["prompt"]
            params.setdefault("remove_background", True)
            warnings += list(self.t2i.info.licence.warnings)
            source = "text"
        job.image_png = B.png_bytes(image)
        self._ensure_loaded("backend", job)
        if self.backend.needs_pil:
            from PIL import Image
            if isinstance(image, bytes):
                image = Image.open(io.BytesIO(image))
                image.load()
        elif not isinstance(image, bytes):
            image = job.image_png

        def progress(p: float, stage: str) -> None:
            job.progress, job.stage = max(job.progress, 0.1 + 0.85 * p), stage

        glb = self.backend.generate(image, quality, seed, params, progress)
        if not glb[:4] == b"glTF":
            raise RuntimeError("backend did not return a GLB")
        job.glb = glb
        job.result = {"format": "glb", "bytes": len(glb), "source": source, "seed": seed, "quality": quality,
                      "backend": self.backend.info.name,
                      "text_to_image": self.t2i.info.name if source == "text" else None,
                      "textured": self.backend.info.textured,
                      "licence": {"model": self.backend.info.licence.name,
                                  "commercial": self.backend.info.licence.commercial,
                                  "text_to_image": self.t2i.info.licence.name if source == "text" else None},
                      "warnings": warnings}

    # ---- API helpers -------------------------------------------------------------------------------------
    def submit(self, req: dict[str, Any]) -> Job:
        if not (req.get("prompt") or req.get("image_b64")):
            raise HTTPException(400, "send prompt (text to 3D) or image_b64 (image to 3D)")
        if req.get("quality", "standard") not in B.QUALITIES:
            raise HTTPException(400, f"quality must be one of {B.QUALITIES}")
        if req.get("image_b64") and len(req["image_b64"]) > MAX_IMAGE_BYTES * 4 // 3:
            raise HTTPException(413, "image too large")
        job = Job(req)
        with self.lock:
            self.jobs[job.id] = job
            done = [j for j in self.jobs.values() if j.status in ("done", "error", "cancelled")]
            for old in sorted(done, key=lambda j: j.created)[:max(0, len(self.jobs) - MAX_JOBS_KEPT)]:
                self.jobs.pop(old.id, None)
        self.q.put(job)
        return job

    def get(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, f"no job {job_id}")
        return job

    def info(self) -> dict[str, Any]:
        gpu: dict[str, Any] = {}
        torch = sys.modules.get("torch")
        if torch is not None and torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            gpu = {"name": torch.cuda.get_device_name(0), "free_gb": round(free / 2**30, 2),
                   "total_gb": round(total / 2**30, 2)}
        return {"ok": True, "backend": self.backend.info.as_dict(), "text_to_image": self.t2i.info.as_dict(),
                "loaded": self.loaded, "queue": self.q.qsize(),
                "running": [j.id for j in self.jobs.values() if j.status == "running"], "gpu": gpu,
                "qualities": list(B.QUALITIES), "available": B.catalog()}


def create_app(service: Service) -> FastAPI:
    app = FastAPI(title="UnrealMCP gen3d", version="1.0")

    @app.middleware("http")
    async def auth(request: Request, call_next):
        if service.token and request.url.path != "/health":
            given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not secrets.compare_digest(given, service.token):
                return JSONResponse({"detail": "bad or missing bearer token"}, status_code=401)
        return await call_next(request)

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/info")
    def info():
        return service.info()

    @app.post("/generate", status_code=202)
    def generate(req: dict[str, Any]):
        return service.submit(req).view()

    @app.get("/jobs/{job_id}")
    def job(job_id: str):
        return service.get(job_id).view()

    @app.get("/jobs/{job_id}/result")
    def result(job_id: str):
        j = service.get(job_id)
        if j.status != "done" or j.glb is None:
            raise HTTPException(409, f"job {job_id} is {j.status}")
        return Response(j.glb, media_type="model/gltf-binary",
                        headers={"Content-Disposition": f'attachment; filename="{job_id}.glb"'})

    @app.get("/jobs/{job_id}/image")
    def image(job_id: str):
        j = service.get(job_id)
        if j.image_png is None:
            raise HTTPException(409, "no conditioning image yet")
        return Response(j.image_png, media_type="image/png")

    @app.delete("/jobs/{job_id}")
    def cancel(job_id: str):
        j = service.get(job_id)
        if j.status == "queued":
            j.status = "cancelled"
        service.jobs.pop(job_id, None)
        return {"ok": True}

    return app


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", default=os.environ.get("GEN3D_BACKEND", "mock"), choices=sorted(B.BACKENDS))
    ap.add_argument("--t2i", default=os.environ.get("GEN3D_T2I", "none"), choices=sorted(B.TEXT_TO_IMAGE))
    ap.add_argument("--host", default=os.environ.get("GEN3D_HOST", "127.0.0.1"),
                    help="0.0.0.0 to serve the LAN (set --token then)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("GEN3D_PORT", "8765")))
    ap.add_argument("--token", default=os.environ.get("GEN3D_TOKEN") or None)
    ap.add_argument("--preload", action="store_true", help="load the models now instead of on the first job")
    ap.add_argument("--list", action="store_true", help="print backends with licences and VRAM, then exit")
    a = ap.parse_args(argv)
    if a.list:
        for kind, items in B.catalog().items():
            print(kind)
            for name, i in items.items():
                print(f"  {name:13} {i['vram_gb']:>5.1f} GB  {i['licence']['name']}  [{i['platforms']}]")
        return
    if a.host not in ("127.0.0.1", "localhost", "::1") and not a.token:
        print("warning: serving beyond localhost without --token: anyone on the network can use the GPU",
              file=sys.stderr)
    service = Service(a.backend, a.t2i, a.token)
    if a.preload:
        service.preload(t2i=True)
    import uvicorn
    uvicorn.run(create_app(service), host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
