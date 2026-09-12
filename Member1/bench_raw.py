"""Raw MobileCLIP-S0 image-encoder speed on this device.

Times ONLY model.encode_image() on a random 1x3x256x256 tensor that already lives on the device.
No screen capture, change detection, PIL, resize/crop, text prompts, similarity, threads or dashboard.
The model is loaded exactly as in monitor.py (official checkpoint, frozen, eval, inference_mode).
"""

import argparse
import platform
import statistics
import time
from pathlib import Path

import torch

from monitor import DEFAULT_CHECKPOINT, LINE, cpu_name, ensure_checkpoint, fmt, load_model, ms, sync

IMAGE_SIZE = 256  # image_size in mobileclip_s0.json
BLOCK = 30        # iterations per block in the stability row


def pct(values, q):
    return values[0] if len(values) < 2 else statistics.quantiles(values, n=100, method="inclusive")[q - 1]


def bench(checkpoint, device, args):
    model, _, _ = load_model(checkpoint, device)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    x = torch.rand(args.batch, 3, IMAGE_SIZE, IMAGE_SIZE, device=device)
    amp = args.amp and device.type == "cuda"

    def once():
        # Synchronize on both sides: GPU work is asynchronous, so without it the timer would
        # stop when the work was queued, not when it finished.
        sync(device)
        t0 = time.perf_counter()
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.float16, enabled=amp):
            model.encode_image(x)
        sync(device)
        return ms(time.perf_counter() - t0)

    for _ in range(args.warmup):
        once()                                   # discarded: CUDA/cuDNN set-up, lazy init
    times = []
    for _ in range(args.iters):
        times.append(once())
        if args.pause_ms:
            time.sleep(args.pause_ms / 1000.0)   # outside the timer: mimics a sparse, monitor-like duty cycle

    print(f"\n{device.type.upper()}  (batch {args.batch}, {'float16 autocast' if amp else 'float32'}, "
          f"pause {args.pause_ms} ms, "
          f"trainable params: {trainable}, warm-up {args.warmup} discarded, {args.iters} timed)")
    print("-" * 63)
    print(f"Mean:      {fmt(statistics.fmean(times))}")
    print(f"Median:    {fmt(statistics.median(times))}")
    print(f"Min:       {fmt(min(times))}")
    print(f"P95:       {fmt(pct(times, 95))}")
    print(f"P99:       {fmt(pct(times, 99))}")
    print(f"Max:       {fmt(max(times))}")
    print(f"Std dev:   {fmt(statistics.stdev(times) if len(times) > 1 else None)}")
    print(f"Rate:      {1000.0 * args.batch / statistics.fmean(times):.1f} images/sec (back-to-back)")
    blocks = [statistics.median(times[i:i + BLOCK]) for i in range(0, len(times) - BLOCK + 1, BLOCK)]
    print(f"Median per {BLOCK}-iteration block (ms), in run order:")
    print("  " + "  ".join(f"{b:.1f}" for b in blocks))
    return statistics.median(times)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", choices=["auto", "cpu", "cuda", "both"], default="both")
    ap.add_argument("--iters", type=int, default=300)
    ap.add_argument("--warmup", type=int, default=20)
    ap.add_argument("--batch", type=int, default=1)
    ap.add_argument("--pause-ms", type=int, default=0,
                    help="idle time between timed iterations (default 0 = back-to-back)")
    ap.add_argument("--amp", action="store_true", help="float16 autocast on CUDA (default: float32, as in monitor.py)")
    ap.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    args = ap.parse_args()

    have_cuda = torch.cuda.is_available()
    if args.device == "both":
        names = ["cuda", "cpu"] if have_cuda else ["cpu"]
    elif args.device == "auto":
        names = ["cuda" if have_cuda else "cpu"]
    else:
        names = [args.device]

    checkpoint = ensure_checkpoint(args.checkpoint)
    print(LINE)
    print(f"Python:            {platform.python_version()}")
    print(f"PyTorch:           {torch.__version__} (CUDA build: {torch.version.cuda})")
    print(f"OS:                {platform.platform()}")
    print(f"CPU:               {cpu_name()}  (torch threads: {torch.get_num_threads()})")
    print(f"GPU:               {torch.cuda.get_device_name(0) if have_cuda else 'none detected'}")
    print(f"Devices:           {', '.join(names)}")
    print(f"Model:             MobileCLIP-S0 image encoder, {checkpoint.name} ({checkpoint.stat().st_size / 1e6:.1f} MB)")
    print(f"Input:             random tensor {args.batch}x3x{IMAGE_SIZE}x{IMAGE_SIZE}, created once on the device")
    if args.amp and "cuda" not in names:
        print("Note: --amp only applies to CUDA and is ignored here.")
    print(LINE)

    medians = {n: bench(checkpoint, torch.device(n), args) for n in names}
    if len(medians) == 2:
        print(f"\nMedian: GPU {medians['cuda']:.1f} ms vs CPU {medians['cpu']:.1f} ms")
    print(LINE)
    print("Raw model time only. The monitor's total processing also includes capture, change detection and preprocessing.")


if __name__ == "__main__":
    main()
