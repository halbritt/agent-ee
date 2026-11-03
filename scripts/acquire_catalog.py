#!/usr/bin/env python3
"""Fetch jlcparts volumes on the host before offline capture: the current snapshot (--refresh)
or the volumes of a recorded source manifest (--manifest, --out)."""
from agent_ee.catalog_feed import main

if __name__ == '__main__':
    main()
