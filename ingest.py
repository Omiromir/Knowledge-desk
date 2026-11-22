"""Compatibility entry point for the local index build."""

import sys

from backend.rag_index import ingest as _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
else:
    sys.modules[__name__] = _implementation
