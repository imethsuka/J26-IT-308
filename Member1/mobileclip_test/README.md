# MobileCLIP-S0 current-screen test

A tiny console program for seeing what MobileCLIP-S0 does with your screen. No benchmark, no loop,
no dashboard: it captures the screen once and prints every step.

## Run

Uses the environment in `Member1/.venv` (see `../README.md` for setup):

```powershell
cd Member1
.venv\Scripts\python mobileclip_test\test_current_screen.py
```

You get a 5-second countdown so you can switch to the window you want to test (otherwise it
would just screenshot the terminal). Options:

```powershell
... test_current_screen.py --delay 8                                   # more time to switch
... test_current_screen.py --crop left                                 # left-aligned square instead of the middle
... test_current_screen.py --crop center left right                    # compare all three views of ONE screenshot
... test_current_screen.py --extra "a screenshot of a video game"      # add your own description (repeatable)
... test_current_screen.py --device cpu
```

## What it does

1. Captures the primary monitor (held in memory; a copy is saved as `last_screenshot.png`).
2. Chooses a square part of the screen (`--crop`): `center` (the official preprocessing: resize the short
   side to 256 px, then centre-crop, which drops the left and right edges), `left` (the leftmost
   1080x1080 pixels, full height) or `right`. Then resizes it to 256x256. What the model really sees is
   saved as `last_model_view_<view>.png`. Open it. It is small and blurry.
3. The image encoder turns that picture into one **image embedding**: a list of 512 numbers.
4. The text encoder turns each description (for example "a screenshot of someone writing computer code")
   into a **text embedding**: also 512 numbers, in the same "space".
5. **Cosine similarity** measures how closely each text embedding points the same way as the image
   embedding. Higher means more similar.
6. Prints all scores, the top 3, and the margin between #1 and #2.

## How to read the output

- Scores around 0.2-0.3 are normal for CLIP-style models. They are **not probabilities** and do not
  add up to 1. What matters is the ranking and the gaps.
- The margin is a similarity gap, not a confidence. A tiny margin (say 0.003) means the model
  finds two descriptions almost equally close.
- The descriptions are experimental probes. Change them with `--extra` and watch the ranking move.
- MobileCLIP does not know what "VS Code" or "Chrome" are unless a description makes them
  similar to the picture. It compares a picture with sentences. It does not know what you are doing.

## Things worth trying

Open each of these full-screen, run the script, then open `last_screenshot.png` and check whether the
top result is sensible: VS Code, a PDF, PowerPoint, Chrome, File Explorer, a video. Also try a screen
with two things at once, as the first run showed: an editor and a terminal on screen at the same time.

## Files

`last_screenshot.png` and `last_model_view_*.png` are overwritten on every run and are git-ignored,
because a screenshot can contain private information. Delete them when you are done.
