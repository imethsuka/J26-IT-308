"""PyInstaller entry for worker.exe: the capture-time worker (listener -> features -> model -> SQLite)."""
import multiprocessing

from main import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
