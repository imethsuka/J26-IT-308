# MobileCLIP-S0 Desktop Monitor (prototype)

A small, single-file experiment (`monitor.py`) that samples the Windows desktop, detects meaningful
screen changes, and runs **frozen MobileCLIP-S0** image encoding on the newest changed screen,
while a live terminal dashboard shows timings and image-text similarity.

## What this prototype is and is not

- **Purpose:** measuring the *feasibility* of on-device visual encoding. The question is "how long
  does MobileCLIP-S0 take on this machine, and can it keep up with a changing desktop?"
- **MobileCLIP-S0 produces visual embeddings.** It does not generate natural-language
  explanations or descriptions of the screen.
- **Image-text similarity is not reliable desktop UI recognition.** The "Top result" is just the
  prompt whose text embedding is most similar (cosine) to the image embedding. In the first local
  runs, top scores were only ≈0.25. Treat the top prompt as a probe output, not a label.
- **The 8 text prompts are experimental probes,** not a validated label set.
- **MobileCLIP-S0 is frozen.** It uses the official Apple checkpoint in `eval()` mode, with all parameters set to
  `requires_grad=False` and inference under `torch.inference_mode()`. No fine-tuning or training takes place.
- **No PCCILM exists in this prototype.** There is also no continual learning, MLP, OCR, privacy
  detection, or database.
- **No importance decision is made.** Every meaningful change is simply encoded (or dropped if stale).
- **Results are hardware-dependent.** All latencies are measured locally with `time.perf_counter()`
  (with `torch.cuda.synchronize()` on GPU). They are not Apple's published numbers and will differ across
  CPUs, GPUs, drivers, power plans and screen resolutions.

## Pipeline

```
Desktop sampler (mss, every --interval-ms)
      ↓
Change detector (64x36 greyscale thumbnail diff; no MobileCLIP)
      ↓
Latest-context buffer (holds at most ONE pending screen; newer replaces older → "dropped/stale")
      ↓
MobileCLIP worker (one thread; official preprocess → encode_image → cosine vs 8 prompt embeddings)
      ↓
Terminal dashboard (redraws in place)
```

- Screenshots are held in memory only; nothing is written to disk.
- **Change detection** downsamples each frame to a 64×36 greyscale thumbnail and compares it with the
  thumbnail of the last *accepted* change. A change is "meaningful" when more than `--threshold`
  (default 1 %) of the cells differ by more than 12 grey levels. Otherwise the sample is logged as
  `NO SIGNIFICANT CHANGE` and MobileCLIP is not run.
- **Queue behaviour:** if a new change arrives while the worker is busy and another change is already
  pending, the older pending one is discarded and counted as *dropped/stale*. The backlog can never
  exceed 2 (1 in progress + 1 pending).
- Text-prompt embeddings are computed once at startup. One warm-up inference runs before
  statistics are collected and is excluded.
- The official MobileCLIP-S0 preprocessing resizes the short side to 256 and **center-crops to
  256×256**. On a 16:9 screen, the left and right edges are therefore not seen by the model.

## Setup (Windows, Python 3.13)

```powershell
cd Member1
python -m venv .venv
.venv\Scripts\pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128   # or omit --index-url for CPU-only
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install --no-deps git+https://github.com/apple/ml-mobileclip.git
```

`--no-deps` skips ml-mobileclip's training/benchmark extras (`datasets`, `clip-benchmark`). Inference
does not need them, and on Python 3.13 they make pip backtrack for a very long time.
`create_model_and_transforms` reparameterizes the model for inference by default, as upstream does.

On first run the official checkpoint (`mobileclip_s0.pt`) is downloaded from Apple
(`docs-assets.developer.apple.com/ml-research/datasets/mobileclip/`) into `checkpoints/`.
The model is the original **MobileCLIP-S0** (`apple/ml-mobileclip`, `mobileclip_s0`), not MobileCLIP2-S0.

## Usage

```powershell
.venv\Scripts\python monitor.py                     # run until Ctrl+C
.venv\Scripts\python monitor.py --device cpu        # force CPU
.venv\Scripts\python monitor.py --interval-ms 250 --duration 60
```

| Flag | Default | Meaning |
|---|---|---|
| `--interval-ms` | 500 | screen sampling interval |
| `--device` | auto | `auto` (CUDA if available), `cpu`, `cuda` |
| `--threshold` | 0.01 | fraction of thumbnail cells that must change |
| `--duration` | 0 | stop automatically after N seconds (0 = until Ctrl+C) |
| `--checkpoint` | `checkpoints/mobileclip_s0.pt` | checkpoint path |
| `--refresh-ms` | 250 | dashboard redraw interval |

Press **Ctrl+C** to stop. A final summary is then printed.

## Raw model speed (`bench_raw.py`)

Times only `model.encode_image()` on a random 1x3x256x256 tensor already on the device: no capture,
change detection, PIL preprocessing, prompts, threads or dashboard. It warms up first, synchronizes the
GPU around every timed call, and prints mean/median/min/P95/P99/max plus a per-block median row that
shows drift over the run.

```powershell
.venv\Scripts\python bench_raw.py                           # GPU and CPU, back-to-back
.venv\Scripts\python bench_raw.py --device cuda --iters 90 --pause-ms 500   # monitor-like duty cycle
.venv\Scripts\python bench_raw.py --device cuda --amp       # float16 autocast
```

First local results (RTX 2050 laptop on AC power, Balanced plan, batch 1, float32):
GPU back-to-back median 14.5 ms, CPU median 41.0 ms, GPU with a 500 ms pause between calls median 76.6 ms
(the GPU idles at 210 MHz between calls). Idle GPU clocks can therefore dominate the monitor's numbers. Run
`nvidia-smi --query-gpu=clocks.sm,pstate --format=csv -l 1` alongside to check.

## Metrics

| Metric | Definition |
|---|---|
| Capture time | `mss` grab of the primary monitor + conversion to a PIL image |
| Change detection | thumbnail + diff against reference |
| MobileCLIP preprocess | official transform (resize, crop, to tensor) + copy to device |
| MobileCLIP inference | `encode_image` + normalisation + similarity vs prompts |
| Total processing | capture + detection + preprocess + inference (excludes time waiting in the buffer) |
| End-to-end latency | from capture start until the similarity result is ready (includes buffer wait) |
| Processing rate | processed contexts during the last 10 s ÷ 10 |
| Samples / Skipped | every capture / captures with no meaningful change |
| Meaningful changes / Processed / Dropped | changes detected / changes encoded / changes discarded as stale |
