"""Double-click source entry for Windows without a console window."""
from seekwords import main

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
