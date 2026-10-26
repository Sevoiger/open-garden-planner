"""Application version lookup without importing the Qt update worker."""

import sys


def get_current_version() -> str:
    if getattr(sys, "frozen", False):
        try:
            from open_garden_planner import _version

            return str(_version.__version__)
        except ImportError:
            pass
    from open_garden_planner import __version__

    return __version__
