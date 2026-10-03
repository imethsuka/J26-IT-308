"""Standalone resource measurement for the MobileCLIP-S0 image encoder.

Reports, on CPU by default:
  * per-call latency (median / p95 / mean / min / max) over N ``embed_frame`` calls, and
  * peak RSS memory at three points: before loading, after load (before first inference), and
    after the first inference.

Results print as a table and append one row to a CSV, so runs on different machines accumulate in
one file for reporting.

Usage:
    python -m eval.measure_encoder --checkpoint path/to/mobileclip_s0.pt --image shot.png \
        [--calls 100] [--device cpu] [--csv results.csv] [--label "desktop-i5"]

Memory is read from the OS (Windows GetProcessMemoryInfo / POSIX getrusage), so no third-party
profiler is required. Run it as its own process to keep the numbers clean; importing it into a
larger program would fold that program's allocations into the RSS figures.
"""

from __future__ import annotations

import argparse
import csv
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow running both as "python -m eval.measure_encoder" and "python eval/measure_encoder.py".
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from features.mobileclip_encoder import EMBED_DIM, load_image_encoder  # noqa: E402
from eval.resource_probe import current_rss_bytes, peak_rss_bytes, reset_peak_rss  # noqa: E402

MB = 1024 * 1024


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--checkpoint", help="path to mobileclip_s0.pt (else $MOBILECLIP_S0_CHECKPOINT)")
    p.add_argument("--image", required=True, help="representative screenshot to embed")
    p.add_argument("--calls", type=int, default=100, help="timed embed_frame calls (default 100)")
    p.add_argument("--warmup", type=int, default=1, help="untimed warm-up calls before timing (default 1)")
    p.add_argument("--device", default="cpu", help="torch device (default cpu)")
    p.add_argument("--csv", default="encoder_measurements.csv", help="CSV file to append a row to")
    p.add_argument("--label", default=platform.node(), help="machine label recorded in the CSV")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    image_path = Path(args.image)
    if not image_path.is_file():
        print(f"error: image not found: {image_path}", file=sys.stderr)
        return 2

    from PIL import Image

    image = Image.open(image_path).convert("RGB")
    image.load()
    image_size = f"{image.width}x{image.height}"

    reset_peak_rss()
    rss_before = current_rss_bytes()

    print(f"Loading MobileCLIP-S0 image encoder on {args.device} ...")
    load_start = time.perf_counter()
    try:
        encoder = load_image_encoder(args.checkpoint, device=args.device)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    load_seconds = time.perf_counter() - load_start
    rss_after_load = current_rss_bytes()
    peak_after_load = peak_rss_bytes()

    # First inference is timed separately: it pays lazy CUDA/CPU kernel init and allocation.
    first_start = time.perf_counter()
    vector = encoder.embed_frame(image)
    first_ms = (time.perf_counter() - first_start) * 1000.0
    rss_after_first = current_rss_bytes()

    _validate_vector(vector)

    for _ in range(max(0, args.warmup - 1)):
        encoder.embed_frame(image)

    latencies_ms = []
    for _ in range(args.calls):
        t0 = time.perf_counter()
        encoder.embed_frame(image)
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)

    peak_overall = peak_rss_bytes()
    stats = _latency_stats(latencies_ms)

    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "label": args.label,
        "os": platform.platform(),
        "python": platform.python_version(),
        "torch": _torch_version(),
        "device": args.device,
        "model": "mobileclip_s0",
        "embed_dim": EMBED_DIM,
        "image_size": image_size,
        "calls": args.calls,
        "load_seconds": round(load_seconds, 3),
        "first_infer_ms": round(first_ms, 3),
        "median_ms": round(stats["median"], 3),
        "p95_ms": round(stats["p95"], 3),
        "mean_ms": round(stats["mean"], 3),
        "min_ms": round(stats["min"], 3),
        "max_ms": round(stats["max"], 3),
        "rss_before_mb": round(rss_before / MB, 1),
        "rss_after_load_mb": round(rss_after_load / MB, 1),
        "rss_after_first_infer_mb": round(rss_after_first / MB, 1),
        "peak_rss_after_load_mb": round(peak_after_load / MB, 1),
        "peak_rss_mb": round(peak_overall / MB, 1),
    }

    _print_report(row)
    csv_path = Path(args.csv)
    _append_csv(csv_path, row)
    print(f"\nAppended 1 row to {csv_path.resolve()}")
    return 0


def _latency_stats(samples_ms: list[float]) -> dict[str, float]:
    ordered = sorted(samples_ms)
    return {
        "median": statistics.median(ordered),
        "p95": _percentile(ordered, 95),
        "mean": statistics.fmean(ordered),
        "min": ordered[0],
        "max": ordered[-1],
    }


def _percentile(ordered: list[float], pct: int) -> float:
    if not ordered:
        return float("nan")
    rank = max(0, min(len(ordered) - 1, (pct * len(ordered) + 99) // 100 - 1))
    return ordered[rank]


def _validate_vector(vector) -> None:
    import numpy as np

    if vector.shape != (EMBED_DIM,):
        raise SystemExit(f"expected {EMBED_DIM}-d vector, got shape {vector.shape}")
    if vector.dtype != np.float32:
        raise SystemExit(f"expected float32 vector, got {vector.dtype}")
    norm = float(np.linalg.norm(vector))
    if abs(norm - 1.0) > 1e-3:
        raise SystemExit(f"vector is not unit-normalized (L2 norm = {norm:.6f})")


def _torch_version() -> str:
    try:
        import torch

        return torch.__version__
    except Exception:  # pragma: no cover - torch is required to run, but keep the report robust
        return "unknown"


def _print_report(row: dict) -> None:
    print("\nMobileCLIP-S0 image-encoder resource measurement")
    print("-" * 56)
    print(f"  machine        : {row['label']}")
    print(f"  os / python    : {row['os']} / py{row['python']}")
    print(f"  torch / device : {row['torch']} / {row['device']}")
    print(f"  image          : {row['image_size']}   embedding: {row['embed_dim']}-d")
    print(f"  model load     : {row['load_seconds']:.2f} s")
    print("\n  Latency over %d calls (ms)" % row["calls"])
    print(f"    first (cold) : {row['first_infer_ms']:.2f}")
    print(f"    median       : {row['median_ms']:.2f}")
    print(f"    p95          : {row['p95_ms']:.2f}")
    print(f"    mean         : {row['mean_ms']:.2f}")
    print(f"    min / max    : {row['min_ms']:.2f} / {row['max_ms']:.2f}")
    print("\n  Peak RSS memory (MB)")
    print(f"    before load        : {row['rss_before_mb']:.1f}")
    print(f"    after load         : {row['rss_after_load_mb']:.1f}  (peak {row['peak_rss_after_load_mb']:.1f})")
    print(f"    after 1st inference: {row['rss_after_first_infer_mb']:.1f}")
    print(f"    peak overall       : {row['peak_rss_mb']:.1f}")


def _append_csv(path: Path, row: dict) -> None:
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if write_header:
            writer.writeheader()
        writer.writerow(row)


if __name__ == "__main__":
    raise SystemExit(main())
