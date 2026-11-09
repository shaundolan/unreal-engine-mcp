# gen3d: local text/image → 3D server

A small FastAPI server that wraps open-weight image-to-3D models, plus an optional text-to-image model for
text-to-3D, behind one API. UnrealMCP's `mesh_generate` tool talks to it over HTTP, so it can run on the same machine
or on a GPU box elsewhere on the LAN (for example a Windows RTX 4090). Everything runs locally and costs nothing per
asset.

```
mesh_generate ──HTTP──▶ gen3d_server.py ──▶ [text→image] ──▶ image→3D ──▶ GLB
      │                                                               │
      ◀──────────────────────────── GLB ◀─────────────────────────────┘
      ▼
headless Blender: cleanup · decimate · UVs · PBR bake · scale · pivot  ──▶  Unreal import · LODs · collision
```

## Backends and licences

Checked against the upstream LICENSE files in November 2026. This is not legal advice: read the licence before you
ship assets. `mesh_generate` returns the generating model's licence and its warnings with every asset.

| `--backend` | Model | Licence | Commercial use | VRAM (typical) | OS | Output |
|---|---|---|---|---|---|---|
| `triposr` | [TripoSR](https://github.com/VAST-AI-Research/TripoSR) (Stability AI + Tripo) | MIT | **Yes** | ~6 GB (less with `TRIPOSR_CHUNK=4096`) | Windows, Linux | vertex colours, no UVs (baked to a texture by the post-process) |
| `sf3d` | [Stable Fast 3D](https://github.com/Stability-AI/stable-fast-3d) | Stability AI Community License | **Only under US$1M annual revenue** (enterprise licence above). Gated weights. | ~6 GB | Linux; Windows experimental (VS 2022) | UV-unwrapped, PBR textures |
| `hunyuan3d` | [Hunyuan3D 2.1](https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1) (Tencent) | Tencent Hunyuan 3D 2.1 Community License | **Not licensed in the EU, the UK or South Korea** (model *and* outputs). Elsewhere it's allowed, with a Tencent licence needed above 1M MAU. | 10 GB shape-only; 21 GB texture; 29 GB both | Windows, Linux, macOS | PBR textures (or shape only, `texture: false`) |
| `trellis2` | [TRELLIS.2-4B](https://github.com/microsoft/TRELLIS.2) (Microsoft) | MIT for the model and code, but its texture export needs nvdiffrast/nvdiffrec under the **NVIDIA Source Code License (non-commercial)** | Check the dependency licences first | ≥ 24 GB (upstream tests on A100/H100) | Linux only (use WSL2 on Windows) | PBR textures, highest fidelity |
| `mock` | procedural barrel or T-pose humanoid | MIT | Yes | 0 | any | vertex colours (for tests) |

| `--t2i` | Model | Licence | Commercial | VRAM |
|---|---|---|---|---|
| `flux-schnell` | [FLUX.1 [schnell]](https://huggingface.co/black-forest-labs/FLUX.1-schnell) | Apache-2.0 | Yes | ~13 GB with CPU offload |
| `sdxl` | [SDXL base 1.0](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0) | CreativeML Open RAIL++-M | Yes, within the use restrictions | ~9 GB |
| `none` | image input only | | | 0 |

Which to pick:
- **Default (safe and fast):** `triposr` + `flux-schnell`. Both are commercial-friendly and fit a 24 GB card
  together. Geometry is soft, so treat the output as props and background dressing.
- **Better textures, if you qualify:** `sf3d` (revenue under US$1M).
- **Best geometry:** `hunyuan3d`, but **not if you are in the EU/UK/South Korea**, Ireland included.
- **Highest fidelity, non-commercial:** `trellis2` on Linux or WSL2 with the whole 24 GB.
- Newer open models appear monthly (for example Tencent ARC's Pixal3D, MIT, May 2026). Adding one is a ~40-line
  `Backend` subclass in `backends.py`.

Weights download from Hugging Face on the first job into `~/.cache/huggingface` (`HF_HOME` moves it). Budget
roughly 2 GB for TripoSR, 4 GB for SF3D, 30 GB for Hunyuan3D 2.1, 16 GB for TRELLIS.2 and 33 GB for FLUX.1-schnell.

## API

All endpoints take and return JSON. When the server has a token, send `Authorization: Bearer <token>`.

| | |
|---|---|
| `GET /info` | backend and text-to-image model, licences, VRAM, GPU, queue |
| `POST /generate` | `{"prompt": "...", "image_b64": "...", "quality": "draft\|standard\|high", "seed": 0, "params": {...}}` → `202 {"job_id": ...}` |
| `GET /jobs/{id}` | `{"status": "queued\|running\|done\|error", "progress", "stage", "error", "result": {licence, warnings, ...}}` |
| `GET /jobs/{id}/result` | the GLB |
| `GET /jobs/{id}/image` | the conditioning image (the text-to-image output, or your input) |
| `DELETE /jobs/{id}` | |

A single worker thread owns the GPU, so jobs run one at a time.

## Setup: Windows + RTX 4090

1. Install the NVIDIA driver, Python 3.11 (python.org, "Add to PATH"), Git, and the
   **CUDA Toolkit 12.4** plus **Visual Studio 2022 Build Tools** ("Desktop development with C++"). TripoSR's
   `torchmcubes` and SF3D's extensions compile against them.
2. In PowerShell:
   ```powershell
   cd C:\gen3d
   git clone https://github.com/shaundolan/unreal-engine-mcp.git
   py -3.11 -m venv venv; .\venv\Scripts\Activate.ps1
   pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
   pip install -r unreal-engine-mcp\servers\gen3d\requirements.txt
   git clone https://github.com/VAST-AI-Research/TripoSR.git
   pip install -r TripoSR\requirements.txt          # builds torchmcubes (needs step 1's toolchain)
   $env:TRIPOSR_DIR = "C:\gen3d\TripoSR"
   huggingface-cli login                            # FLUX.1-schnell / SF3D are gated: accept their terms first
   ```
3. Run it and let the LAN reach it:
   ```powershell
   $env:GEN3D_TOKEN = "<a long random string>"
   python unreal-engine-mcp\servers\gen3d\gen3d_server.py --backend triposr --t2i flux-schnell --host 0.0.0.0 --port 8765 --preload
   New-NetFirewallRule -DisplayName "gen3d 8765" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow -Profile Private
   ```
4. On the machine running the MCP server:
   `UNREAL_MCP_GEN3D_URL=http://<4090-host>:8765` and `UNREAL_MCP_GEN3D_TOKEN=<the token>`, then call `gen3d_status`.

Other backends on Windows: for SF3D, clone it, `pip install -r requirements.txt` (experimental on Windows) and set
`SF3D_DIR`. For Hunyuan3D 2.1 (not licensed in the EU/UK), clone it, follow its README to build `custom_rasterizer`
and `DifferentiableRenderer`, set `HUNYUAN3D_DIR`, and on 24 GB pass `backend_params={"texture": false}` (shape only;
Blender bakes vertex colour or the material). For TRELLIS.2, use WSL2 Ubuntu with the Linux steps below.

## Setup: Linux

```bash
python3.11 -m venv ~/gen3d && . ~/gen3d/bin/activate
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
pip install -r servers/gen3d/requirements.txt
git clone https://github.com/VAST-AI-Research/TripoSR ~/TripoSR && pip install -r ~/TripoSR/requirements.txt
python servers/gen3d/gen3d_server.py --backend triposr --t2i flux-schnell
# TRELLIS.2 (24 GB, Linux):
git clone -b main --recursive https://github.com/microsoft/TRELLIS.2 ~/TRELLIS.2
cd ~/TRELLIS.2 && . ./setup.sh --new-env --basic --flash-attn --nvdiffrast --nvdiffrec --cumesh --o-voxel --flexgemm
python /path/to/servers/gen3d/gen3d_server.py --backend trellis2 --t2i none   # inside that conda env
```

An 8 GB card (GTX 1080 class) runs `triposr` with `--t2i none` and `TRIPOSR_CHUNK=4096` when ~6 GB is free.
Text-to-image does not fit next to it there, so pass an image instead.

Without a GPU, `--backend mock` exercises the whole pipeline (the UnrealMCP tests do exactly that).

## Environment

| Variable | Meaning |
|---|---|
| `GEN3D_BACKEND`, `GEN3D_T2I`, `GEN3D_HOST`, `GEN3D_PORT`, `GEN3D_TOKEN` | defaults for the CLI flags |
| `TRIPOSR_DIR`, `SF3D_DIR`, `HUNYUAN3D_DIR`, `TRELLIS2_DIR` | model repo checkouts (default `~/<RepoName>`) |
| `TRIPOSR_MODEL`, `SF3D_MODEL`, `HUNYUAN3D_MODEL`, `TRELLIS2_MODEL`, `FLUX_MODEL`, `SDXL_MODEL` | Hugging Face ids or local paths |
| `TRIPOSR_CHUNK` | TripoSR renderer chunk size (lower uses less VRAM) |

`python gen3d_server.py --list` prints every backend with its licence and VRAM.
