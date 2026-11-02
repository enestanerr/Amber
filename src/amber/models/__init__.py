"""Model backends (``ModelBackend`` protocol) and model-ID resolution.

This is one of the only two packages allowed to know about Claude (the other is
``amber.adapters``); nothing in ``trace``/``align``/``compile``/``runtime`` imports it.
"""

from __future__ import annotations
