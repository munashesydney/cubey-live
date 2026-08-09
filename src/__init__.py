"""
Cubey — Gemini Live Voice & Interruption Simulator package.

Subpackages stay independently importable so lightweight modules (e.g. src.db)
can be used without pulling in the GUI/audio stack.
"""

__version__ = "1.0.0"

# Kept lazy so `import src.db` does not drag in the tkinter/audio stack.
_LAZY_EXPORTS = {"ApplicationController": "src.controller"}


def __getattr__(name):
    if name in _LAZY_EXPORTS:
        import importlib

        module = importlib.import_module(_LAZY_EXPORTS[name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["ApplicationController"]
