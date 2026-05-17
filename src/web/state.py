"""Shared mutable state for the web API.

Both api.py and the routers import shared globals from here.
api.py writes to this module during startup.
Routers read from it at call time (lazy lookup).
"""
from storage import get_store, init_store

__all__ = ["get_store", "init_store"]
