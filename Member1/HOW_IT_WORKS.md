# What the MobileCLIP-S0 Desktop Monitor actually does

## In one paragraph

`monitor.py` looks at your screen every 500 ms. Most of the time nothing much has changed, and it
does nothing else. When the screen has changed, it hands a screenshot to MobileCLIP-S0, a small
pre-trained image model, and compares the result against eight fixed sentences such as "a screenshot
of a web browser". It shows which sentence scored highest, and how long every step took, on a
dashboard that redraws in place.

The point is **not** to understand your desktop. The point is to answer one engineering question:
*can this model keep up with a changing desktop on this machine, and how much time does it cost?*

## What happens on every sample

```
every 500 ms
  1. Capture      grab the primary monitor into memory (nothing is saved to disk)
  2. Detect       has the screen meaningfully changed since the last accepted change?
        no  -> log "NO SIGNIFICANT CHANGE", stop here (the model is not run)
        yes -> 3
  3. Hand off     put the screenshot in a one-slot buffer
  4. Encode       the worker thread runs MobileCLIP-S0 on it
  5. Score        compare the image against the 8 prompts, keep the best one
  6. Display      the dashboard shows timings, the top prompt and its score
```

Two threads do the work. The **sampler** does steps 1-3 on a fixed schedule. The **worker** does
steps 4-5 and only ever takes the newest screenshot. The main thread just redraws the dashboard.

## Step 2: how "meaningful change" is decided

MobileCLIP is not used for this, because the check has to be cheap and run on every sample.

- The screenshot is shrunk to a **64×36 greyscale thumbnail**.
- It is compared, cell by cell, with the thumbnail of the **last accepted change**.
- A cell counts as changed if its brightness differs by more than 12 (out of 255).
- If more than **1 %** of cells changed (`--threshold`), the screen has meaningfully changed.

Comparing against the last *accepted* frame, not the previous frame, means a slow drift (a fade,
a scrolling page) still adds up and eventually triggers. The very first sample always counts as a
change.

## Step 3: why frames get dropped

The buffer between the sampler and the worker holds **one** pending screenshot. If a new change
arrives while the worker is still busy and an older one is already waiting, the older one is thrown
away and counted as **Dropped/stale**. The worker therefore always works on the freshest screen, and
the backlog can never exceed 2 (one being processed plus one waiting). If dropped/stale keeps
climbing, the model cannot keep up at that sampling rate.

## Step 4-5: what MobileCLIP-S0 computes

MobileCLIP-S0 is a CLIP-style model. It has an image encoder and a text encoder that map into the
same space.

1. **At startup (once):** the 8 prompts are turned into 8 vectors.
2. **Per changed screen:** the screenshot is resized and centre-cropped to 256×256, and the image
   encoder turns it into one vector.
3. Both kinds of vector are normalised, so a dot product is a **cosine similarity**.
4. The prompt with the highest similarity is shown as "Top result", along with that score.

The model is used exactly as Apple ships it: frozen weights, evaluation mode, no gradients, no
training or fine-tuning of any kind.

## What the numbers do and do not tell you

**Trustworthy (measured locally with `time.perf_counter()`):**

| Number | What it covers |
|---|---|
| Capture time | grabbing the screen and converting it to an image |
| Change detection | thumbnail and comparison |
| MobileCLIP preprocess | resize/crop/tensor and copy to the device |
| MobileCLIP inference | image encoding, normalising, similarity against the 8 prompts |
| Total processing | the four above added up (excludes time spent waiting in the buffer) |
| End-to-end latency | from the start of capture until the result is ready (includes the wait) |

**Not trustworthy yet:**

- The **Top result** is just the best of 8 options. It always picks *something*, even when none of
  the prompts describe the screen, and the scores are close together (about 0.25 in the first runs).
- The model never sees the left and right edges of a widescreen display, because of the centre crop.
- When the last sample was "no change", the dashboard still shows the top result from the last
  processed screenshot. It is not re-evaluated.
- The model produces vectors, not sentences. It cannot explain what is on the screen.

## What the first runs showed (RTX 2050 laptop, i5-11400H)

These are single short runs on one machine, so treat them as an illustration.

| | GPU, 500 ms, 40 s | CPU, 100 ms, 25 s |
|---|---|---|
| Samples / meaningful changes | 81 / 58 | 253 / 69 |
| Processed / dropped | 58 / 0 | 67 / 2 |
| Median inference | 76.7 ms | 69.7 ms |
| P95 inference | 77.4 ms | 141.8 ms |
| Mean total processing | 141.6 ms | 138.4 ms |
| Max backlog | 1 | 2 |

- Inference was fast on the GPU at first (about 15-17 ms) and then settled near 77 ms. The raw
  benchmark (`bench_raw.py`) shows why: the model alone takes about 14.5 ms when the GPU is kept
  busy, but with a 500 ms pause between inferences the GPU drops to its idle power state (P8,
  210 MHz according to `nvidia-smi`) and the same call takes about 76 ms. The monitor's sparse
  workload triggers exactly that, so its GPU inference time is mostly the idle clock, not the model.
- Screen capture alone took about 35-50 ms per frame, which is a large share of the cost.
- The CPU stress run at 100 ms did drop stale frames (2), which shows the drop logic working.

## What is deliberately not here

No PCCILM, continual learning, MLP training, OCR, privacy detection, database, or any decision
about which screens are *important*. The program only measures the cost of visual encoding.

## Running it

```powershell
cd Member1
.venv\Scripts\python monitor.py                  # run until Ctrl+C, then print the summary
.venv\Scripts\python monitor.py --device cpu     # force CPU
.venv\Scripts\python monitor.py --interval-ms 250 --threshold 0.02
```

See [README.md](README.md) for setup and the full list of options.
