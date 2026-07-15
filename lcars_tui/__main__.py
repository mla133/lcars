if __package__ in (None, ""):
    # Allow `python lcars_tui\__main__.py` or `python .\lcars_tui\` (which
    # runs this file without package context) in addition to the normal
    # `python -m lcars_tui` invocation.
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from lcars_tui.app import main
else:
    from .app import main

if __name__ == "__main__":
    main()
