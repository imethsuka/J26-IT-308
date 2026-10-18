r"""Run only the memory-writing stages on memory.db: sentences for important memories, then session and day notes.

    python memory_llm.py --memory-db %LOCALAPPDATA%\context-importance\memory.db --llm-model <path to .gguf> [--max-entries 60]
    adapt.exe memory-llm --memory-db ... --llm-model ...          (the installed app)

This is stages 7 and 8 of the learning job (adaptation/summarizer.py, adaptation/rollups.py) without the learning cycle, so it
never labels records, trains or deploys a model. It reads only page text that went through the privacy filter, and the local
model runs on this PC, on a dedicated GPU when one is usable and otherwise on the CPU (adaptation/llm_runtime.py). Someone
asked for this run, so unlike the background job it does not pause when the user is active. Safe to run again: only what is
missing is written.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--memory-db", required=True)
    parser.add_argument("--llm-model", required=True, help="a GGUF chat model file, or a folder of them")
    parser.add_argument("--max-entries", type=int, default=25, help="most entries to summarise in this run")
    parser.add_argument("--max-sessions", type=int, default=12, help="most session notes to write in this run")
    parser.add_argument("--days", type=int, default=14, help="how far back sessions are looked at")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    model_path, db = Path(args.llm_model), Path(args.memory_db)
    if not (model_path.is_file() or any(model_path.glob("*.gguf"))) or not db.exists():
        print("model file or memory.db not found", file=sys.stderr)
        return 2

    from adaptation.rollups import RollupConfig, RollupJob, run_rollups
    from adaptation.llm_runtime import LocalModel
    from adaptation.summarizer import SummarizeConfig, SummarizeJob, run_summaries
    from storage.memory_store import MemoryStore

    memory = MemoryStore(db)
    shared_model = LocalModel(model_path, background=False)

    def progress(done: int, total: int) -> None:
        # one line the dashboard reads to show "12 of 40"; flushed so it arrives while the model is still working
        print("PROGRESS " + json.dumps({"stage": "summaries", "done": done, "total": total}), flush=True)

    started = time.perf_counter()
    try:
        summaries = run_summaries(SummarizeJob(memory, shared_model, SummarizeConfig(max_entries=args.max_entries), progress=progress))
        print("summaries:", json.dumps(summaries), flush=True)
        print("PROGRESS " + json.dumps({"stage": "notes", "done": 0, "total": 0}), flush=True)
        rollups = run_rollups(RollupJob(memory, shared_model, RollupConfig(days=args.days, max_sessions=args.max_sessions)))
    finally:
        shared_model.close()
    print("rollups:  ", json.dumps(rollups), flush=True)
    print(f"took {time.perf_counter() - started:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
