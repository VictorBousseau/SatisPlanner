"""SatisPlanner: a steady-state factory planner for Satisfactory 1.2.

Layering rule, enforced by ``tests/test_architecture.py``:

    ui  -->  core  <--  data

``core`` is a pure domain layer: it never imports Qt, and it never reads the
game database. Data reaches it by injection.
"""

# The major number tracks the document schema, and only that. A schema 7 file is
# refused by a 2.0 build exactly as a schema 6 file was refused by 1.1: the file is
# the only interface this application exposes to its own past, and breaking it is
# what a major number is for. Applying the rule to one feature rather than to six is
# the point -- a rule that bends for a small change is not a rule.
#
# The **database** schema is a different thing and does not move the major number.
# Widening the catalogue -- the Blender, and keeping the recipes no node can place --
# took `db.SCHEMA_VERSION` from 7 to 8 and left every saved factory readable, both
# ways: the file is the interface to the past, and that change did not touch it.
# The resource well does: it is a node kind, so a 4.0 document holds something a 3.x
# build cannot read, and the major number is exactly the sentence that says so.
# Variable power does not, for the same reason the Blender did not: three machines
# and 43 recipes joined the catalogue, and a document still holds nothing but the
# class name of a recipe. A 4.0 build opens a 4.1 file and vice versa.
# The geothermal generator does: schema 9, and 5.0. The nuclear plant rides along
# without moving anything -- it is an ordinary generator node, and what changed is
# what the engine does with one rather than what the document says about it.
# English does not, and 5.1 is the whole point: a factory saved by a French session
# and one saved by an English session are the same bytes, which is checked rather
# than hoped for. What moved is the base -- schema 12, two columns for the English
# halves of a blurb and a building name -- and the base is shipped with the build,
# so nobody has an old one. A 5.0 file opens in 5.1 and the other way round.
# (This is not the game's version, which is 1.2 and lives in ``data.db.GAME_VERSION``.)
__version__ = "5.1.0"
