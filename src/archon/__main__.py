"""Enable ``python -m archon`` as an alias for the ``archon`` console script.

Used as the fallback dispatch path by the subagent wrapper
(``.claude/tools/archon-subagent.py``) when the console script isn't on PATH
— e.g. inside the Codex sandbox, whose shell PATH omits the venv ``bin/`` even
though the venv's site-packages (and therefore this module) are importable.
"""

from archon.cli import app

if __name__ == "__main__":
    app()
