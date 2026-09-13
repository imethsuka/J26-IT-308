"""Does the ONNX encoder give the same embeddings as the torch one?

Everything stored so far (embeddings, the k-means prototypes, trained models) was made with torch, so the
ONNX encoder is only safe to ship if its vectors stay essentially identical. Checks, on real frames:

1. cosine similarity and max absolute difference, ONNX vs torch, per frame (torch is run live here);
2. against the **stored** torch embeddings in a cache (``--cache``, keyed by file name stem), if given;
3. the effect on what Model 1 sees: cosine to each saved prototype set (``--prototypes``), the nearest
   prototype, and the resulting ``vis_proto_similarity`` / ``vis_proto_distance`` differences;
4. speed and memory, each measured in its own fresh process (``--measure``).

    python -m eval.verify_onnx_encoder --onnx mobileclip_s0_image.onnx --images D:\\...\\screenshots --n 60 \\
        --checkpoint ..\\Member1\\checkpoints\\mobileclip_s0.pt --cache drc_embeddings.npz --prototypes prototypes.npy

Only aggregate numbers are printed; no image or title is shown or copied.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MB = 1024 * 1024


def compare(a: np.ndarray, b: np.ndarray) -> dict[str, float]:
    """Per-pair agreement between two ``(N, 512)`` unit-vector sets."""
    cosine = np.sum(a * b, axis=1) / (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1))
    return {
        "n": float(len(a)),
        "cosine_min": float(cosine.min()),
        "cosine_mean": float(cosine.mean()),
        "max_abs_diff": float(np.abs(a - b).max()),
    }


def prototype_effect(a: np.ndarray, b: np.ndarray, prototypes: np.ndarray) -> dict[str, float]:
    """How much switching encoders changes Model 1's visual inputs, against saved prototypes."""
    protos = prototypes / np.linalg.norm(prototypes, axis=1, keepdims=True)
    sim_a, sim_b = a @ protos.T, b @ protos.T
    return {
        "nearest_prototype_same": float(np.mean(sim_a.argmax(1) == sim_b.argmax(1))),
        "proto_similarity_max_abs_diff": float(np.abs(sim_a.max(1) - sim_b.max(1)).max()),
        "proto_similarity_mean_abs_diff": float(np.abs(sim_a.max(1) - sim_b.max(1)).mean()),
    }


def load_frames(folder: Path, n: int, seed: int) -> list[Path]:
    files = sorted(folder.glob("*.jpg"))
    random.Random(seed).shuffle(files)
    return files[:n]


def _measure_child(kind: str, model: str, image: str, calls: int) -> None:
    """Runs in a fresh process: prints a JSON line with load time, latency and RSS."""
    from PIL import Image

    from eval.resource_probe import current_rss_bytes, peak_rss_bytes

    before = current_rss_bytes()
    t0 = time.perf_counter()
    if kind == "onnx":
        from features.onnx_encoder import OnnxImageEncoder

        encoder = OnnxImageEncoder(model)
    else:
        from features.mobileclip_encoder import load_image_encoder

        encoder = load_image_encoder(model)
    load = time.perf_counter() - t0
    after_load = current_rss_bytes()
    frame = Image.open(image).convert("RGB")
    encoder.embed_frame(frame)  # first call: warm-up
    times = []
    for _ in range(calls):
        t = time.perf_counter()
        encoder.embed_frame(frame)
        times.append((time.perf_counter() - t) * 1000)
    print(json.dumps({
        "kind": kind, "load_s": round(load, 2), "median_ms": round(float(np.median(times)), 1),
        "p95_ms": round(float(np.percentile(times, 95)), 1),
        "rss_before_mb": round(before / MB), "rss_after_load_mb": round(after_load / MB),
        "rss_steady_mb": round(current_rss_bytes() / MB), "peak_rss_mb": round(peak_rss_bytes() / MB),
    }))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--onnx", required=False)
    parser.add_argument("--checkpoint", default=None)
    parser.add_argument("--images", default=None, help="folder of .jpg frames (read-only)")
    parser.add_argument("--n", type=int, default=60)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--cache", default=None, help=".npz of stored torch embeddings with arrays 'ids' (file stems) and 'embeddings'")
    parser.add_argument("--prototypes", default=None, help="prototypes.npy from a model version")
    parser.add_argument("--measure", action="store_true", help="also measure speed and memory, ONNX vs torch")
    parser.add_argument("--calls", type=int, default=50)
    parser.add_argument("--child", nargs=4, metavar=("KIND", "MODEL", "IMAGE", "CALLS"), help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.child:
        _measure_child(args.child[0], args.child[1], args.child[2], int(args.child[3]))
        return

    from PIL import Image

    from features.onnx_encoder import OnnxImageEncoder

    frames = load_frames(Path(args.images), args.n, args.seed)
    onnx = OnnxImageEncoder(args.onnx)
    images = [Image.open(f).convert("RGB") for f in frames]
    onnx_vectors = np.stack([onnx.embed_frame(im) for im in images])
    print(f"frames: {len(frames)}  input size {images[0].size}")

    torch_vectors = None
    if args.checkpoint:
        from features.mobileclip_encoder import load_image_encoder

        torch_encoder = load_image_encoder(args.checkpoint)
        torch_vectors = np.stack([torch_encoder.embed_frame(im) for im in images])
        print("ONNX vs torch (live):", {k: round(v, 7) for k, v in compare(onnx_vectors, torch_vectors).items()})
        del torch_encoder

    if args.cache:
        cache = np.load(args.cache, allow_pickle=False)
        stored = dict(zip((str(i) for i in cache["ids"]), cache["embeddings"]))
        hit = [(i, stored[f.stem]) for i, f in enumerate(frames) if f.stem in stored]
        if hit:
            ours = np.stack([onnx_vectors[i] for i, _ in hit])
            theirs = np.stack([v for _, v in hit]).astype(np.float32)
            print(f"ONNX vs stored torch embeddings ({len(hit)} frames in cache):",
                  {k: round(v, 7) for k, v in compare(ours, theirs).items()})
            if args.prototypes:
                protos = np.load(args.prototypes)
                print("effect on prototype features:", {k: round(v, 6) for k, v in prototype_effect(ours, theirs, protos).items()})
        else:
            print("no frame of this sample is in the cache")

    if args.measure and args.checkpoint:
        print("\nspeed and memory (fresh process each):")
        for kind, model in (("torch", args.checkpoint), ("onnx", args.onnx)):
            out = subprocess.run(
                [sys.executable, "-m", "eval.verify_onnx_encoder", "--child", kind, str(model), str(frames[0]), str(args.calls)],
                capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent),
            )
            print(" ", out.stdout.strip().splitlines()[-1] if out.stdout.strip() else out.stderr[-400:])


if __name__ == "__main__":
    main()
