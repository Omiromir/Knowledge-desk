"""Compatibility entry point for local index search."""

import sys

from backend.rag_index import retrieve as _implementation

if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation
