"""Convenience launcher: ``python main.py`` (equivalent to ``python -m satisplanner``)."""

from satisplanner.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
