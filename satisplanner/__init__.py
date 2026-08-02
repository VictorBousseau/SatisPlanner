"""SatisPlanner: a steady-state factory planner for Satisfactory 1.2.

Layering rule, enforced by ``tests/test_architecture.py``:

    ui  -->  core  <--  data

``core`` is a pure domain layer: it never imports Qt, and it never reads the
game database. Data reaches it by injection.
"""

# 2.0 rather than 1.2: the document schema went from 4 to 6 over these six lots, so a
# factory saved today is refused by a 1.1 build. The file is the only interface this
# application exposes to its own past, and breaking it is what a major number is for.
# (It is also not the game's version, which is 1.2 and lives in ``data.db.GAME_VERSION``.)
__version__ = "2.0.0"
