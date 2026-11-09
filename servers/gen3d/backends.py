"""Generation backends behind one interface: image -> GLB bytes, plus optional text -> image.

Every model is imported lazily inside its backend, so the server starts (and `--backend mock` works) with
only fastapi + uvicorn + pillow installed. Licences are recorded here and returned by /info and with every job,
so the MCP tool can warn when a model's terms restrict commercial use or territory.

Checked 2026-11 against the upstream LICENSE files. Not legal advice: read the licence before shipping assets.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from glb_writer import fixture_glb


@dataclass(frozen=True)
class Licence:
    name: str
    url: str
    commercial: str          # "yes" | "conditional" | "no"
    summary: str
    warnings: tuple[str, ...] = ()


@dataclass
class Info:
    name: str
    kind: str                # "image_to_3d" | "text_to_image"
    licence: Licence
    vram_gb: float           # typical peak at the default quality
    textured: str            # what the GLB carries: "pbr_uv" | "basecolor_uv" | "vertex_color" | "none"
    platforms: str
    repo: str
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["licence"]["warnings"] = list(self.licence.warnings)
        return d


MIT = "https://opensource.org/license/mit"
NVDIFFRAST_NC = ("TRELLIS.2 needs nvdiffrast / nvdiffrec, which are under the NVIDIA Source Code License "
                 "(non-commercial use only, except by NVIDIA). The model is MIT, but the full texturing pipeline "
                 "is not cleared for commercial use.")

LICENCES = {
    "mock": Licence("MIT (this repo)", MIT, "yes", "Procedural test meshes, no model."),
    "triposr": Licence("MIT", "https://huggingface.co/stabilityai/TripoSR", "yes",
                       "TripoSR code and weights (Stability AI + Tripo AI) are MIT: commercial use allowed."),
    "sf3d": Licence("Stability AI Community License", "https://stability.ai/community-license-agreement",
                    "conditional",
                    "Free for research, non-commercial use, and commercial use by individuals or organisations "
                    "with under US$1M annual revenue; above that an enterprise licence is required.",
                    ("Stable Fast 3D: commercial use only while your annual revenue is under US$1M "
                     "(Stability AI Community License); the Hugging Face weights are gated.",)),
    "hunyuan3d": Licence("Tencent Hunyuan 3D 2.1 Community License", "https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1/blob/main/LICENSE",
                         "conditional",
                         "Royalty-free, but the licence does NOT apply in the European Union, the United Kingdom or "
                         "South Korea: use of the model or its outputs there is unlicensed. Products above 1M monthly "
                         "active users need a separate licence from Tencent.",
                         ("Hunyuan3D 2.1: the licence excludes the EU, UK and South Korea (model AND outputs); "
                          "do not use it there. Over 1M MAU needs a Tencent licence.",)),
    "trellis2": Licence("MIT (model + code); NVIDIA Source Code License for nvdiffrast/nvdiffrec",
                        "https://github.com/microsoft/TRELLIS.2", "conditional",
                        "Microsoft TRELLIS.2-4B weights and code are MIT. Its texture export depends on NVIDIA "
                        "nvdiffrast/nvdiffrec (non-commercial licence).", (NVDIFFRAST_NC,)),
    "flux-schnell": Licence("Apache-2.0", "https://huggingface.co/black-forest-labs/FLUX.1-schnell", "yes",
                            "FLUX.1 [schnell] weights are Apache-2.0: commercial use allowed."),
    "sdxl": Licence("CreativeML Open RAIL++-M", "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0",
                    "yes", "SDXL base 1.0: commercial use allowed under the RAIL++-M use restrictions."),
    "none": Licence("n/a", "", "yes", "No text-to-image model: image input only."),
}

QUALITIES = ("draft", "standard", "high")
PROMPT_SUFFIX = (", a single {kind}, centered, full view, three-quarter angle, plain white background, "
                 "soft even studio lighting, no shadows, no text, high detail 3D asset concept")


def solid_png(size: int = 64, rgb: tuple[int, int, int] = (200, 200, 200)) -> bytes:
    """A flat PNG without Pillow (the mock path runs with the MCP server's dependencies only)."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"".join(b"\0" + bytes(rgb) * size for _ in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _png(image) -> bytes:
    if isinstance(image, bytes):
        return image
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _add_repo(env: str, default: str) -> Path:
    """Model repos aren't pip packages: put their checkout on sys.path."""
    root = Path(os.environ.get(env, default)).expanduser()
    if not root.is_dir():
        raise RuntimeError(f"{env}={root} does not exist: clone the model repo there (see servers/gen3d/README.md)")
    for p in (root, *[root / s for s in ("hy3dshape", "hy3dpaint")]):
        if p.is_dir() and str(p) not in sys.path:
            sys.path.insert(0, str(p))
    return root


class Backend:
    info: Info
    needs_pil = True  # False: generate() gets the raw image bytes

    def load(self) -> None:  # heavy model load, once, on the worker thread
        pass

    def generate(self, image, quality: str, seed: int, params: dict[str, Any], progress) -> bytes:
        raise NotImplementedError


class MockBackend(Backend):
    """No model: returns a procedural barrel, or a T-pose humanoid when the prompt/image name asks for a
    character. For CI, for the fake editor, and to test the pipeline on a machine without a big GPU."""

    info = Info("mock", "image_to_3d", LICENCES["mock"], 0.0, "vertex_color", "any", "",
                "Deterministic fixtures: 'barrel' or 'humanoid'.")
    needs_pil = False
    HUMANOID_WORDS = ("human", "person", "character", "man", "woman", "knight", "soldier", "hero", "robot", "figure")

    def generate(self, image, quality, seed, params, progress):
        hint = str(params.get("_hint", "")).lower()
        kind = params.get("mock_kind") or ("humanoid" if any(w in hint for w in self.HUMANOID_WORDS) else "barrel")
        progress(0.5, "mock geometry")
        return fixture_glb(kind)


class TripoSRBackend(Backend):
    info = Info("triposr", "image_to_3d", LICENCES["triposr"], 6.0, "vertex_color",
                "Windows, Linux (CUDA); CPU works but slowly", "https://github.com/VAST-AI-Research/TripoSR",
                "Fastest (~1 s on a 4090). Geometry is soft; colours are per vertex, so the post-process bakes them "
                "to a texture. Lower VRAM with TRIPOSR_CHUNK=4096. Also runs on 8 GB cards.")
    MC_RES = {"draft": 192, "standard": 256, "high": 384}

    def load(self):
        _add_repo("TRIPOSR_DIR", "~/TripoSR")
        import torch
        from tsr.system import TSR
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = TSR.from_pretrained(os.environ.get("TRIPOSR_MODEL", "stabilityai/TripoSR"),
                                         config_name="config.yaml", weight_name="model.ckpt")
        self.model.renderer.set_chunk_size(int(os.environ.get("TRIPOSR_CHUNK", "8192")))
        self.model.to(self.device)

    def generate(self, image, quality, seed, params, progress):
        import numpy as np
        import torch
        import trimesh
        from PIL import Image
        from tsr.utils import remove_background, resize_foreground
        if params.get("remove_background", True):
            import rembg
            image = resize_foreground(remove_background(image, rembg.new_session()), params.get("foreground", 0.85))
            a = np.asarray(image).astype(np.float32) / 255.0
            a = a[:, :, :3] * a[:, :, 3:4] + (1 - a[:, :, 3:4]) * 0.5
            image = Image.fromarray((a * 255.0).astype(np.uint8))
        torch.manual_seed(seed)
        progress(0.3, "triposr inference")
        with torch.no_grad():
            codes = self.model([image.convert("RGB")], device=self.device)
        progress(0.7, "marching cubes")
        res = int(params.get("mc_resolution", self.MC_RES[quality]))
        mesh = self.model.extract_mesh(codes, True, resolution=res)[0]
        # TripoSR's frame -> glTF viewer frame (Y up, front +Z), as its Gradio app does before display.
        mesh.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
        mesh.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
        return mesh.export(file_type="glb")


class SF3DBackend(Backend):
    info = Info("sf3d", "image_to_3d", LICENCES["sf3d"], 6.0, "pbr_uv",
                "Linux; Windows experimental (needs VS 2022 build tools)", "https://github.com/Stability-AI/stable-fast-3d",
                "~1 s per asset. UV-unwrapped mesh with base colour + roughness/metallic and normal maps.")
    TEX = {"draft": 512, "standard": 1024, "high": 2048}

    def load(self):
        _add_repo("SF3D_DIR", "~/stable-fast-3d")
        import torch
        from sf3d.system import SF3D
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = SF3D.from_pretrained(os.environ.get("SF3D_MODEL", "stabilityai/stable-fast-3d"),
                                          config_name="config.yaml", weight_name="model.safetensors")
        self.model.to(self.device).eval()

    def generate(self, image, quality, seed, params, progress):
        from contextlib import nullcontext

        import torch
        from sf3d.utils import remove_background, resize_foreground
        image = image.convert("RGBA")
        if params.get("remove_background", True):
            import rembg
            image = remove_background(image, rembg.new_session())
        image = resize_foreground(image, params.get("foreground", 0.85))
        torch.manual_seed(seed)
        progress(0.3, "sf3d inference")
        ctx = torch.autocast(device_type="cuda", dtype=torch.bfloat16) if self.device == "cuda" else nullcontext()
        with torch.no_grad(), ctx:
            mesh, _ = self.model.run_image(image, bake_resolution=int(params.get("texture_size", self.TEX[quality])),
                                           remesh=params.get("remesh", "none"),
                                           vertex_count=int(params.get("vertex_count", -1)))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "mesh.glb"
            mesh.export(str(path), include_normals=True)
            return path.read_bytes()


class Hunyuan3DBackend(Backend):
    info = Info("hunyuan3d", "image_to_3d", LICENCES["hunyuan3d"], 10.0, "pbr_uv",
                "Windows, Linux, macOS", "https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1",
                "Best geometry of the set. Shape ~10 GB VRAM; PBR texture adds ~21 GB (29 GB total), so on a 24 GB "
                "4090 run shape-only (texture=false, then bake in Blender) or enable CPU offload.",
                {"texture_vram_gb": 21.0})
    STEPS = {"draft": 20, "standard": 30, "high": 50}
    OCTREE = {"draft": 256, "standard": 384, "high": 512}

    def load(self):
        _add_repo("HUNYUAN3D_DIR", "~/Hunyuan3D-2.1")
        from hy3dshape.pipelines import Hunyuan3DDiTFlowMatchingPipeline
        self.shape = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
            os.environ.get("HUNYUAN3D_MODEL", "tencent/Hunyuan3D-2.1"))
        self.paint = None

    def generate(self, image, quality, seed, params, progress):
        import torch
        image = image.convert("RGBA")
        if params.get("remove_background", True):
            from hy3dshape.rembg import BackgroundRemover
            image = BackgroundRemover()(image)
        progress(0.2, "hunyuan3d shape")
        mesh = self.shape(image=image, num_inference_steps=int(params.get("steps", self.STEPS[quality])),
                          octree_resolution=int(params.get("octree_resolution", self.OCTREE[quality])),
                          generator=torch.manual_seed(seed))[0]
        with tempfile.TemporaryDirectory() as d:
            shape_path, img_path = Path(d) / "shape.glb", Path(d) / "input.png"
            mesh.export(str(shape_path))
            if not params.get("texture", True):
                return shape_path.read_bytes()
            image.save(img_path)
            progress(0.6, "hunyuan3d paint")
            if self.paint is None:
                from textureGenPipeline import Hunyuan3DPaintConfig, Hunyuan3DPaintPipeline
                self.paint = Hunyuan3DPaintPipeline(Hunyuan3DPaintConfig(max_num_view=6, resolution=512))
            out = self.paint(str(shape_path), image_path=str(img_path),
                             output_mesh_path=str(Path(d) / "textured.obj"))
            return _to_glb(out)


class Trellis2Backend(Backend):
    info = Info("trellis2", "image_to_3d", LICENCES["trellis2"], 24.0, "pbr_uv",
                "Linux only (upstream tested on A100/H100; a 24 GB 4090 is the floor)", "https://github.com/microsoft/TRELLIS.2",
                "Highest-fidelity PBR output; needs >= 24 GB and Linux (WSL2 on the Windows box).")
    DECIMATE = {"draft": 100_000, "standard": 300_000, "high": 1_000_000}

    def load(self):
        _add_repo("TRELLIS2_DIR", "~/TRELLIS.2")
        os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
        from trellis2.pipelines import Trellis2ImageTo3DPipeline
        model = os.environ.get("TRELLIS2_MODEL", "microsoft/TRELLIS.2-4B")
        self.pipe = Trellis2ImageTo3DPipeline.from_pretrained(model)
        self.pipe.cuda()

    def generate(self, image, quality, seed, params, progress):
        import o_voxel
        progress(0.2, "trellis2 inference")
        try:
            mesh = self.pipe.run(image, seed=seed)[0]
        except TypeError:  # older checkouts take no seed
            mesh = self.pipe.run(image)[0]
        mesh.simplify(16_777_216)  # nvdiffrast's face limit
        progress(0.7, "trellis2 glb export")
        glb = o_voxel.postprocess.to_glb(
            vertices=mesh.vertices, faces=mesh.faces, attr_volume=mesh.attrs, coords=mesh.coords,
            attr_layout=mesh.layout, voxel_size=mesh.voxel_size, aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
            decimation_target=int(params.get("decimation_target", self.DECIMATE[quality])),
            texture_size=int(params.get("texture_size", 2048)), remesh=True, remesh_band=1, remesh_project=0,
            verbose=False)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "mesh.glb"
            glb.export(str(path), extension_webp=False)
            return path.read_bytes()


def _to_glb(path: str) -> bytes:
    import trimesh
    return trimesh.load(path, force="scene").export(file_type="glb")


# ---- text -> image -------------------------------------------------------------------------------------
class TextToImage:
    info: Info

    def load(self) -> None:
        pass

    def generate(self, prompt: str, seed: int, quality: str):
        raise NotImplementedError


class NoTextToImage(TextToImage):
    info = Info("none", "text_to_image", LICENCES["none"], 0.0, "none", "any", "")


class MockTextToImage(TextToImage):
    info = Info("mock", "text_to_image", LICENCES["mock"], 0.0, "none", "any", "", "A grey placeholder image.")

    def generate(self, prompt, seed, quality):
        return solid_png()


class FluxSchnell(TextToImage):
    info = Info("flux-schnell", "text_to_image", LICENCES["flux-schnell"], 13.0, "none", "Windows, Linux (CUDA)",
                "https://huggingface.co/black-forest-labs/FLUX.1-schnell",
                "4 steps. ~33 GB of weights in bf16; with model CPU offload it peaks around 13 GB VRAM on a 4090.")
    STEPS = {"draft": 2, "standard": 4, "high": 4}

    def load(self):
        import torch
        from diffusers import FluxPipeline
        self.pipe = FluxPipeline.from_pretrained(os.environ.get("FLUX_MODEL", "black-forest-labs/FLUX.1-schnell"),
                                                 torch_dtype=torch.bfloat16)
        self.pipe.enable_model_cpu_offload()

    def generate(self, prompt, seed, quality):
        import torch
        return self.pipe(prompt, num_inference_steps=self.STEPS[quality], guidance_scale=0.0, height=1024, width=1024,
                         max_sequence_length=256, generator=torch.Generator("cpu").manual_seed(seed)).images[0]


class SDXL(TextToImage):
    info = Info("sdxl", "text_to_image", LICENCES["sdxl"], 9.0, "none", "Windows, Linux (CUDA)",
                "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0", "Smaller alternative to FLUX.")
    STEPS = {"draft": 15, "standard": 25, "high": 40}

    def load(self):
        import torch
        from diffusers import StableDiffusionXLPipeline
        self.pipe = StableDiffusionXLPipeline.from_pretrained(
            os.environ.get("SDXL_MODEL", "stabilityai/stable-diffusion-xl-base-1.0"), torch_dtype=torch.float16,
            variant="fp16", use_safetensors=True)
        self.pipe.enable_model_cpu_offload()

    def generate(self, prompt, seed, quality):
        import torch
        return self.pipe(prompt, negative_prompt="multiple objects, cropped, text, watermark, busy background",
                         num_inference_steps=self.STEPS[quality],
                         generator=torch.Generator("cpu").manual_seed(seed)).images[0]


BACKENDS: dict[str, type[Backend]] = {"mock": MockBackend, "triposr": TripoSRBackend, "sf3d": SF3DBackend,
                                      "hunyuan3d": Hunyuan3DBackend, "trellis2": Trellis2Backend}
TEXT_TO_IMAGE: dict[str, type[TextToImage]] = {"none": NoTextToImage, "mock": MockTextToImage,
                                               "flux-schnell": FluxSchnell, "sdxl": SDXL}


def catalog() -> dict[str, Any]:
    return {"image_to_3d": {k: v.info.as_dict() for k, v in BACKENDS.items()},
            "text_to_image": {k: v.info.as_dict() for k, v in TEXT_TO_IMAGE.items()}}


def png_bytes(image) -> bytes:
    return _png(image)
