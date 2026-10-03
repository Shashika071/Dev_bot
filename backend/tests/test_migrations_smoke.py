"""Alembic revision chain imports cleanly."""

import os

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_script_directory_loads():
    root = os.path.dirname(os.path.dirname(__file__))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "alembic"))
    scripts = ScriptDirectory.from_config(cfg)
    revs = list(scripts.walk_revisions())
    assert any(r.revision == "001_hardening" for r in revs)
