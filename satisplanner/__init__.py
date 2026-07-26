"""SatisPlanner: a steady-state factory planner for Satisfactory 1.2.

Layering rule, enforced by ``tests/test_architecture.py``:

    ui  -->  core  <--  data

``core`` is a pure domain layer: it never imports Qt, and it never reads the
game database. Data reaches it by injection.
"""

__version__ = "1.1.0"
