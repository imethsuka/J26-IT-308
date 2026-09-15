"""MobileCLIP-S0 demo: which text descriptions is my current screen most similar to?

Capture the screen once, turn it into an "image embedding", turn a few text descriptions into
"text embeddings", and compare them with cosine similarity. Prints every step so you can see what
MobileCLIP actually does. This is image-text similarity, NOT an understanding of what you are doing.
"""

import argparse
import time
import urllib.request
import warnings
from pathlib import Path

import mss
import torch
from PIL import Image
from torchvision.transforms.functional import to_pil_image

warnings.filterwarnings("ignore", category=FutureWarning, module="timm")  # mobileclip uses timm.models.layers
import mobileclip  # noqa: E402

MODEL_NAME = "mobileclip_s0"
CHECKPOINT_URL = "https://docs-assets.developer.apple.com/ml-research/datasets/mobileclip/mobileclip_s0.pt"
CHECKPOINT = Path(__file__).parent.parent / "checkpoints" / "mobileclip_s0.pt"
OUT_DIR = Path(__file__).parent

# (short label shown in the results, text description given to the model)
PROMPTS = [
    ("coding / programming",    "a screenshot of someone writing computer code"),
    ("terminal / command line", "a screenshot of a terminal window with command line text"),
    ("web browsing",            "a screenshot of someone browsing the web"),
    ("reading a document",      "a screenshot of someone reading a PDF document"),
    ("presentation",            "a screenshot of someone creating a presentation"),
    ("spreadsheet",             "a screenshot of a spreadsheet with rows and columns"),
    ("file management",         "a screenshot of someone managing files in a file explorer"),
    ("video / media",           "a screenshot of someone watching a video"),
    ("chat / communication",    "a screenshot of a chat or messaging application"),
    ("design application",      "a screenshot of a graphic design application"),
]


def crop_view(img, mode):
    """The part of the screenshot handed to the official preprocessing (resize short side to 256, centre-crop)."""
    w, h = img.size
    side = min(w, h)
    if mode == "left":
        return img.crop((0, 0, side, side))           # leftmost square, full height
    if mode == "right":
        return img.crop((w - side, 0, w, side))       # rightmost square, full height
    return img                                        # "center": the official preprocessing crops the middle


def crop_note(img, mode):
    w, h = img.size
    side = min(w, h)
    return {"left": f"pixels x 0-{side} of {w}", "right": f"pixels x {w - side}-{w} of {w}",
            "center": f"pixels x {(w - side) // 2}-{(w + side) // 2} of {w}"}[mode]


def show(mode, note, items, scores, encode_ms):
    order = scores.argsort(descending=True).tolist()
    print(f"\nMost similar visual-text descriptions   [view: {mode}, {note}]")
    print("-" * 63)
    for rank, i in enumerate(order, 1):
        s = scores[i].item()
        print(f"{rank:>2}. {items[i][0][:32]:<32} {s:.4f}  {'#' * max(0, round(s * 100))}")
    print("Top 3:  " + "   ".join(f"{items[i][0]} ({scores[i].item():.4f})" for i in order[:3]))
    print(f"Margin between #1 and #2: {scores[order[0]].item() - scores[order[1]].item():.4f}"
          "   (a similarity gap, NOT a confidence or probability)")
    print(f"Image encoding: {encode_ms:.1f} ms (one call after one warm-up; not a benchmark)")
    return items[order[0]][0], scores[order[0]].item()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--delay", type=int, default=5, help="seconds to wait before capturing, so you can switch windows")
    ap.add_argument("--crop", nargs="+", choices=["center", "left", "right"], default=["center"],
                    help="which square part of the screen the model sees; give several to compare them on one screenshot")
    ap.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    ap.add_argument("--extra", action="append", default=[], metavar="TEXT",
                    help='add your own description, e.g. --extra "a screenshot of a video game" (repeatable)')
    args = ap.parse_args()

    device = torch.device("cuda" if args.device == "auto" and torch.cuda.is_available() else
                          "cpu" if args.device == "auto" else args.device)
    items = PROMPTS + [(t, t) for t in args.extra]

    print("MobileCLIP-S0 Desktop Context Test")
    print("-" * 63)
    if not CHECKPOINT.exists():
        CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading official MobileCLIP-S0 checkpoint to {CHECKPOINT} ...")
        urllib.request.urlretrieve(CHECKPOINT_URL, CHECKPOINT)
    model, _, preprocess = mobileclip.create_model_and_transforms(MODEL_NAME, pretrained=str(CHECKPOINT))
    model = model.to(device).eval()
    for p in model.parameters():
        p.requires_grad_(False)                     # frozen: we only run it, never train it
    tokenizer = mobileclip.get_tokenizer(MODEL_NAME)
    print(f"Model: MobileCLIP-S0 (frozen)   Device: {device}")

    # ---- 1. capture -------------------------------------------------------------------------------
    print(f"\nSwitch to the window you want to test. Capturing in {args.delay} s...")
    for i in range(args.delay, 0, -1):
        print(f"  {i}...", flush=True)
        time.sleep(1)
    with mss.MSS() as sct:
        shot = sct.grab(sct.monitors[1])            # primary monitor, held in memory
    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    img.save(OUT_DIR / "last_screenshot.png")
    print(f"\n1. Screenshot captured: {img.width}x{img.height}  (saved to mobileclip_test/last_screenshot.png)")

    def sync():
        if device.type == "cuda":
            torch.cuda.synchronize()

    winners = []
    with torch.inference_mode():
        # ---- 2. text side: descriptions -> vectors (in real use this is done once) ---------------------
        text = model.encode_text(tokenizer([t for _, t in items]).to(device))
        text = text / text.norm(dim=-1, keepdim=True)         # length 1, so a dot product = cosine

        for n, mode in enumerate(args.crop):
            # ---- 3. what the model is actually given ---------------------------------------------------
            x = preprocess(crop_view(img, mode)).unsqueeze(0).to(device)   # 256x256, floats 0-1
            to_pil_image(x[0].cpu()).save(OUT_DIR / f"last_model_view_{mode}.png")
            if n == 0:
                print(f"2. Model input: tensor {tuple(x.shape)} = 1 image, 3 colour channels, 256x256 pixels")
                print("   This is all MobileCLIP sees: saved as mobileclip_test/last_model_view_<view>.png")
                model.encode_image(x)               # warm-up call, not timed
                sync()

            # ---- 4. image side: picture -> one vector -------------------------------------------------
            t0 = time.perf_counter()
            image = model.encode_image(x)
            sync()
            encode_ms = (time.perf_counter() - t0) * 1000.0
            raw_norm = image.norm().item()
            image = image / image.norm(dim=-1, keepdim=True)
            scores = (image @ text.T)[0].cpu()                    # one cosine similarity per description

            if n == 0:
                print(f"3. Image embedding: {image.shape[-1]} numbers, e.g. "
                      + ", ".join(f"{v:+.3f}" for v in image[0, :5].tolist()) + f" ...  (length {raw_norm:.2f} before scaling to 1.0)")
                print(f"4. Text embeddings: {len(items)} descriptions x {text.shape[-1]} numbers (same 'space' as the image)")
                print("5. Cosine similarity = how closely the image vector and each text vector point the same way.")
            winners.append((mode, *show(mode, crop_note(img, mode), items, scores, encode_ms)))

    if len(winners) > 1:
        print("\nTop result per view (same screenshot)")
        print("-" * 63)
        for mode, label, score in winners:
            print(f"{mode:<8} {label:<26} {score:.4f}")
    print("\nReminder: this compares a picture with sentences. It does not know what you are doing.")


if __name__ == "__main__":
    main()
