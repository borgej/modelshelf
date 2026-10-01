import multiprocessing

if __name__ == "__main__":
    # Required for the frozen .exe: scanning uses a process pool, and on Windows
    # worker processes start by re-running the program ("spawn"). Without this
    # every worker would open another ModelShelf window.
    multiprocessing.freeze_support()
    from ui.main_window import run
    run()
