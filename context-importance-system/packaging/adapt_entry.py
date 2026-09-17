"""PyInstaller entry for adapt.exe: Model 2, the scheduled learning job (labels -> prototypes -> retrain -> deploy gate)."""
import multiprocessing

from adaptation.scheduled_job import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
