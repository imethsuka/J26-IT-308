"""PyInstaller entry for adapt.exe: Model 2, the scheduled learning job (labels -> prototypes -> retrain -> deploy gate).

``adapt.exe memory-llm ...`` runs only the memory-writing stages instead (python-worker/memory_llm.py): the dashboard's
"Write summaries" button, without needing the repo's Python. ``adapt.exe memory-reset ...`` clears what the model wrote
so it can be written again (adaptation/memory_reset.py). ``adapt.exe champion set <version>`` makes a model the
champion (adaptation/champion.py): the dashboard's Make champion button.
"""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "memory-llm":
        del sys.argv[1]
        from memory_llm import main as memory_llm_main

        raise SystemExit(memory_llm_main())
    if len(sys.argv) > 1 and sys.argv[1] == "memory-reset":
        del sys.argv[1]
        from adaptation.memory_reset import main as memory_reset_main

        raise SystemExit(memory_reset_main())
    if len(sys.argv) > 1 and sys.argv[1] == "champion":
        del sys.argv[1]
        from adaptation.champion import main as champion_main

        raise SystemExit(champion_main())
    from adaptation.scheduled_job import main

    raise SystemExit(main())
