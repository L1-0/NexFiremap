#!/usr/bin/env python
"""Convenience launcher so ``python run.py`` just works."""

from nexfiremap.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
