"""Optional line-profiler hook with zero runtime dependency by default.

Set ``LINE_PROFILE=1`` and install ``leanq[profile]`` to activate the real
``line_profiler.profile`` decorator.  Ordinary leanq installs never import
line_profiler and pay only the cost of a normal Python function call.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import ParamSpec, TypeVar, cast

P = ParamSpec("P")
R = TypeVar("R")

if os.environ.get("LINE_PROFILE") == "1":
    try:
        from line_profiler import profile as _line_profile
    except ImportError as exc:  # pragma: no cover - depends on optional package
        raise RuntimeError(
            "LINE_PROFILE=1 requires line_profiler; install with "
            "`python3 -m pip install -e 'tools/leanq[profile]'`"
        ) from exc

    profile = cast(Callable[[Callable[P, R]], Callable[P, R]], _line_profile)
else:

    def profile(func: Callable[P, R]) -> Callable[P, R]:
        """No-op stand-in for :func:`line_profiler.profile`."""
        return func
