"""Real-time desktop monitor: frozen MobileCLIP-S0 feasibility prototype.

Desktop sampler -> change detector -> latest-context buffer -> MobileCLIP worker -> terminal dashboard

Only measures visual-encoding cost and image-text similarity. Nothing here decides
importance, classifies reliably, learns, or stores data.
"""

import argparse
import os
import platform
import statistics
import sys
import threading
import time
import urllib.request
import warnings
from collections import deque
from datetime import datetime
from pathlib import Path

import mss
import numpy as np
import torch
from PIL import Image

warnings.filterwarnings("ignore", category=FutureWarning, module="timm")  # mobileclip uses timm.models.layers
import mobileclip  # noqa: E402

MODEL_NAME = "mobileclip_s0"
CHECKPOINT_URL = "https://docs-assets.developer.apple.com/ml-research/datasets/mobileclip/mobileclip_s0.pt"
DEFAULT_CHECKPOINT = Path(__file__).parent / "checkpoints" / "mobileclip_s0.pt"

PROMPTS = [
    "a screenshot of a coding environment",
    "a screenshot of a web browser",
    "a screenshot of a research paper",
    "a screenshot of a presentation",
    "a screenshot of a design application",
    "a screenshot of a file manager",
    "a screenshot of a chat application",
    "a screenshot of a video or media application",
]

THUMB_SIZE = (64, 36)   # change-detection thumbnail (w, h)
PIXEL_DIFF = 12         # grey-level difference that counts a thumbnail cell as changed
LINE = "=" * 63
RULE = "-" * 63


def ms(seconds):
    return seconds * 1000.0


def fmt(value, unit=" ms", digits=1):
    return "-" if value is None else f"{value:.{digits}f}{unit}"


# --------------------------------------------------------------------------- setup

def cpu_name():
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        except OSError:
            pass
    return platform.processor() or "unknown"


def ensure_checkpoint(path):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading official MobileCLIP-S0 checkpoint to {path} ...")
        urllib.request.urlretrieve(CHECKPOINT_URL, path)
    return path


def load_model(checkpoint, device):
    model, _, preprocess = mobileclip.create_model_and_transforms(MODEL_NAME, pretrained=str(checkpoint))
    model = model.to(device).eval()
    for p in model.parameters():
        p.requires_grad_(False)
    tokenizer = mobileclip.get_tokenizer(MODEL_NAME)
    with torch.inference_mode():
        text = model.encode_text(tokenizer(PROMPTS).to(device))
        text = text / text.norm(dim=-1, keepdim=True)
    return model, preprocess, text


def grab(sct, monitor):
    shot = sct.grab(monitor)
    return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def thumbnail(img):
    return np.asarray(img.convert("L").resize(THUMB_SIZE, Image.BOX), dtype=np.int16)


def sync(device):
    if device.type == "cuda":
        torch.cuda.synchronize()


def encode(model, preprocess, text_feats, img, device):
    """Returns (preprocess_s, inference_s, top_index, top_score)."""
    t0 = time.perf_counter()
    x = preprocess(img).unsqueeze(0).to(device)
    sync(device)
    t1 = time.perf_counter()
    with torch.inference_mode():
        feats = model.encode_image(x)
        feats = feats / feats.norm(dim=-1, keepdim=True)
        sims = (feats @ text_feats.T)[0]
        idx = int(sims.argmax())
        score = float(sims[idx])  # .item() forces device sync
    sync(device)
    t2 = time.perf_counter()
    return t1 - t0, t2 - t1, idx, score


# --------------------------------------------------------------------------- shared state

class LatestSlot:
    """Holds at most ONE pending context. A newer context replaces (drops) an unprocessed one."""

    def __init__(self, stats):
        self.cond = threading.Condition()
        self.pending = None
        self.busy = False
        self.closed = False
        self.stats = stats

    def put(self, item):
        with self.cond:
            if self.pending is not None:
                self.stats.inc("dropped")
            self.pending = item
            self.stats.note_backlog(1 + int(self.busy))
            self.cond.notify()

    def get(self):
        with self.cond:
            self.busy = False
            while self.pending is None and not self.closed:
                self.cond.wait()
            if self.closed:
                return None
            item, self.pending = self.pending, None
            self.busy = True
            return item

    def backlog(self):
        with self.cond:
            return int(self.pending is not None) + int(self.busy)

    def close(self):
        with self.cond:
            self.closed = True
            self.cond.notify_all()


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.samples = self.changes = self.processed = self.skipped = self.dropped = 0
        self.max_backlog = 0
        self.inference, self.preprocess, self.total, self.e2e = [], [], [], []
        self.processed_at = deque()
        self.recent = deque(maxlen=10)
        self.latest = None
        self.top = None

    def inc(self, name):
        with self.lock:
            setattr(self, name, getattr(self, name) + 1)

    def note_backlog(self, n):
        with self.lock:
            self.max_backlog = max(self.max_backlog, n)

    def add_event(self, event):
        with self.lock:
            self.samples += 1
            if event["event"] == "SCREEN CHANGE":
                self.changes += 1
            else:
                self.skipped += 1
            self.recent.append(event)
            self.latest = event

    def add_result(self, event, pre_s, inf_s, idx, score):
        now = time.perf_counter()
        with self.lock:
            event["preprocess"] = ms(pre_s)
            event["inference"] = ms(inf_s)
            event["total"] = event["capture"] + event["detect"] + ms(pre_s) + ms(inf_s)
            event["e2e"] = ms(now - event["t0"])
            self.processed += 1
            self.inference.append(event["inference"])
            self.preprocess.append(event["preprocess"])
            self.total.append(event["total"])
            self.e2e.append(event["e2e"])
            self.processed_at.append(now)
            self.top = (PROMPTS[idx], score, event["time"])

    def rate(self, window=10.0):
        now = time.perf_counter()
        with self.lock:
            while self.processed_at and now - self.processed_at[0] > window:
                self.processed_at.popleft()
            return len(self.processed_at) / window


# --------------------------------------------------------------------------- threads

def sampler(args, slot, stats, stop):
    interval = args.interval_ms / 1000.0
    reference = None
    with mss.MSS() as sct:
        monitor = sct.monitors[1]
        next_t = time.perf_counter()
        while not stop.is_set():
            t0 = time.perf_counter()
            img = grab(sct, monitor)
            t1 = time.perf_counter()
            thumb = thumbnail(img)
            changed_frac = 1.0 if reference is None else float(np.mean(np.abs(thumb - reference) > PIXEL_DIFF))
            changed = changed_frac > args.threshold
            if changed:
                reference = thumb
            t2 = time.perf_counter()

            event = {
                "t0": t0,
                "time": datetime.now().strftime("%H:%M:%S.%f")[:-3],
                "event": "SCREEN CHANGE" if changed else "NO CHANGE",
                "capture": ms(t1 - t0),
                "detect": ms(t2 - t1),
                "changed_frac": changed_frac,
                "preprocess": None, "inference": None, "total": ms(t2 - t0), "e2e": None,
            }
            stats.add_event(event)
            if changed:
                slot.put((event, img))

            next_t += interval
            delay = next_t - time.perf_counter()
            if delay > 0:
                stop.wait(delay)
            else:
                next_t = time.perf_counter()  # fell behind: don't burst to catch up


def worker(model, preprocess, text_feats, device, slot, stats):
    while True:
        item = slot.get()
        if item is None:
            return
        event, img = item
        pre_s, inf_s, idx, score = encode(model, preprocess, text_feats, img, device)
        stats.add_result(event, pre_s, inf_s, idx, score)


# --------------------------------------------------------------------------- dashboard / summary

def dashboard(args, device_label, stats, slot, status):
    with stats.lock:
        ev = stats.latest
        top = stats.top
        recent = list(stats.recent)
        inf = list(stats.inference)
        tot = list(stats.total)
        counts = (stats.samples, stats.changes, stats.processed, stats.skipped, stats.dropped)
    rate = stats.rate()
    backlog = slot.backlog()

    out = [LINE, " MobileCLIP-S0 Desktop Monitor", LINE, "",
           "Model: MobileCLIP-S0",
           f"Device: {device_label}",
           f"Sampling interval: {args.interval_ms} ms",
           f"Status: {status}", "",
           "Latest event", RULE]
    if ev:
        label = ev["event"] if ev["event"] == "SCREEN CHANGE" else "NO SIGNIFICANT CHANGE"
        out += [f"Timestamp:              {ev['time']}",
                f"Event:                  {label}  ({ev['changed_frac'] * 100:.1f}% cells changed)",
                f"Capture time:           {fmt(ev['capture'])}",
                f"Change detection:       {fmt(ev['detect'])}",
                f"MobileCLIP preprocess:  {fmt(ev['preprocess'])}",
                f"MobileCLIP inference:   {fmt(ev['inference'])}",
                f"Total processing:       {fmt(ev['total'])}",
                f"End-to-end latency:     {fmt(ev['e2e'])}"]
    else:
        out += ["(waiting for first sample)"] + [""] * 7
    out += ["", "Visual similarity (experimental probe, NOT a classification)", RULE,
            f"Top result:             {top[0] if top else '-'}",
            f"Similarity:             {top[1]:.4f}   (frame {top[2]})" if top else "Similarity:             -",
            "", "Recent events", RULE,
            f"{'Time':<14}{'Event':<18}{'Inference':<15}Total", RULE]
    for e in reversed(recent):
        out.append(f"{e['time']:<14}{e['event']:<18}{fmt(e['inference']):<15}{fmt(e['total'])}")
    out += [""] * (10 - len(recent))

    samples, changes, processed, skipped, dropped = counts
    out += ["", "Statistics", RULE,
            f"Samples:                {samples}",
            f"Meaningful changes:     {changes}",
            f"Processed:              {processed}",
            f"Skipped:                {skipped}",
            f"Dropped/stale:          {dropped}", "",
            f"Mean inference:         {fmt(statistics.fmean(inf) if inf else None)}",
            f"Median inference:       {fmt(statistics.median(inf) if inf else None)}",
            f"Min inference:          {fmt(min(inf) if inf else None)}",
            f"Max inference:          {fmt(max(inf) if inf else None)}", "",
            f"Mean total processing:  {fmt(statistics.fmean(tot) if tot else None)}",
            f"Current processing rate: {rate:.1f} contexts/sec (last 10 s)", "",
            f"Queue/backlog:          {backlog}   (max 2 = 1 in progress + 1 pending)",
            LINE, "Ctrl+C to stop"]
    return "\n".join(line.ljust(63) for line in out)


def p95(values):
    if not values:
        return None
    if len(values) < 2:
        return values[0]
    return statistics.quantiles(values, n=20, method="inclusive")[-1]


def summary(stats, runtime_s):
    inf, tot = stats.inference, stats.total
    mean = lambda v: statistics.fmean(v) if v else None
    return "\n".join([
        LINE, " Final summary: MobileCLIP-S0 Desktop Monitor", LINE,
        f"Total runtime:              {runtime_s:.1f} s",
        f"Samples:                    {stats.samples}",
        f"Meaningful changes:         {stats.changes}",
        f"Processed:                  {stats.processed}",
        f"Skipped (no change):        {stats.skipped}",
        f"Dropped/stale:              {stats.dropped}",
        f"Mean inference:             {fmt(mean(inf))}",
        f"Median inference:           {fmt(statistics.median(inf) if inf else None)}",
        f"P95 inference:              {fmt(p95(inf))}",
        f"Max inference:              {fmt(max(inf) if inf else None)}",
        f"Mean preprocessing:         {fmt(mean(stats.preprocess))}",
        f"Mean total processing:      {fmt(mean(tot))}",
        f"Mean end-to-end latency:    {fmt(mean(stats.e2e))}",
        f"Maximum observed backlog:   {stats.max_backlog}",
        LINE,
        "Note: similarity scores are experimental probes, not reliable desktop-context classification.",
    ])


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval-ms", type=int, default=500, help="screen sampling interval (default 500)")
    ap.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    ap.add_argument("--threshold", type=float, default=0.01,
                    help="fraction of 64x36 thumbnail cells that must change (default 0.01)")
    ap.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    ap.add_argument("--duration", type=float, default=0, help="stop after N seconds (0 = until Ctrl+C)")
    ap.add_argument("--refresh-ms", type=int, default=250, help="dashboard redraw interval")
    args = ap.parse_args()

    if args.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(args.device)
    gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none detected"
    device_label = f"GPU ({torch.cuda.get_device_name(device)})" if device.type == "cuda" else "CPU"

    checkpoint = ensure_checkpoint(args.checkpoint)
    print(LINE)
    print(f"Python:            {platform.python_version()}")
    print(f"PyTorch:           {torch.__version__} (CUDA build: {torch.version.cuda})")
    print(f"OS:                {platform.platform()}")
    print(f"CPU:               {cpu_name()}")
    print(f"GPU:               {gpu}")
    print(f"Inference device:  {device_label}")
    print(f"Model:             MobileCLIP-S0 (apple/ml-mobileclip v1, '{MODEL_NAME}')")
    print(f"Checkpoint:        {checkpoint} ({checkpoint.stat().st_size / 1e6:.1f} MB)")
    print("Loading model (frozen, eval mode, inference_mode)...")
    model, preprocess, text_feats = load_model(checkpoint, device)
    print(f"Parameters:        {sum(p.numel() for p in model.parameters()) / 1e6:.1f} M, "
          f"trainable: {sum(p.numel() for p in model.parameters() if p.requires_grad)}")

    with mss.MSS() as sct:
        warm_img = grab(sct, sct.monitors[1])
    pre_s, inf_s, _, _ = encode(model, preprocess, text_feats, warm_img, device)
    print(f"Warm-up inference: preprocess {ms(pre_s):.1f} ms, inference {ms(inf_s):.1f} ms (excluded from stats)")
    print(LINE)
    time.sleep(1.5)

    stats = Stats()
    slot = LatestSlot(stats)
    stop = threading.Event()
    threads = [
        threading.Thread(target=sampler, args=(args, slot, stats, stop), daemon=True),
        threading.Thread(target=worker, args=(model, preprocess, text_feats, device, slot, stats), daemon=True),
    ]

    os.system("")  # enable ANSI escape processing on Windows consoles
    sys.stdout.write("\x1b[2J\x1b[?25l")
    start = time.perf_counter()
    for t in threads:
        t.start()
    try:
        while not args.duration or time.perf_counter() - start < args.duration:
            sys.stdout.write("\x1b[H" + dashboard(args, device_label, stats, slot, "RUNNING") + "\x1b[J")
            sys.stdout.flush()
            time.sleep(args.refresh_ms / 1000.0)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        threads[0].join()
        slot.close()      # worker finishes its current item, then exits
        threads[1].join()
        runtime = time.perf_counter() - start
        sys.stdout.write("\x1b[H" + dashboard(args, device_label, stats, slot, "STOPPED") + "\x1b[J\x1b[?25h\n\n")
        print(summary(stats, runtime))


if __name__ == "__main__":
    main()
