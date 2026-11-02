"""Project root discovery and runtime-state locations.

* The project root is the nearest directory (walking up from the start) that contains
  ``amber.toml``, like ``git`` finds ``.git``. ``--project PATH`` / ``AMBER_PROJECT`` override.
* Runtime state lives in ``AMBER_HOME`` (default ``<root>/.amber``).
"""

from __future__ import annotations

import os
from pathlib import Path

CONFIG_FILENAME = "amber.toml"
HOME_DIRNAME = ".amber"
PROGRAMS_DIRNAME = "programs"

HOME_SUBDIRS = ("traces", "build", "proxy", "logs", "blobs")


def find_project_root(start: Path | None = None, explicit: Path | None = None) -> Path | None:
    """Return the project root or ``None`` when no ``amber.toml`` is found."""
    if explicit is not None:
        explicit = explicit.resolve()
        return explicit if (explicit / CONFIG_FILENAME).is_file() else None
    env = os.environ.get("AMBER_PROJECT")
    if env:
        return find_project_root(explicit=Path(env))
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / CONFIG_FILENAME).is_file():
            return candidate
    return None


def amber_home(root: Path) -> Path:
    """``AMBER_HOME`` if set (relative paths resolve against *root*), else ``<root>/.amber``."""
    env = os.environ.get("AMBER_HOME")
    if env:
        p = Path(env)
        return p if p.is_absolute() else (root / p).resolve()
    return root / HOME_DIRNAME


def programs_dir(root: Path) -> Path:
    """Where compiled program versions are written (committed by the user, not runtime state)."""
    return root / PROGRAMS_DIRNAME


def display_path(path: Path | str, base: Path | None = None) -> str:
    """*path* relative to *base* (default: the cwd) when it lies inside it, else unchanged.

    Messages stay short and do not print the user's home directory (it would end up in
    screenshots and demo recordings).
    """
    p = Path(path)
    try:
        return p.resolve().relative_to((base or Path.cwd()).resolve()).as_posix()
    except (ValueError, OSError):
        return str(p)
