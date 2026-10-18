# -*- coding: utf-8 -*-
"""
Core modules for Douban-Trakt synchronization tool.
Imports are safely wrapped to avoid crashing initialization when optional dependencies are pending installation.
"""

try:
    from .config import Config
except ImportError:
    Config = None

try:
    from .database import LocalDatabase
except ImportError:
    LocalDatabase = None

try:
    from .models import MovieRecord
except ImportError:
    MovieRecord = None

try:
    from .trakt_client import TraktClient
except ImportError:
    TraktClient = None

try:
    from .douban_client import DoubanClient
except ImportError:
    DoubanClient = None

try:
    from .matcher import Matcher, DiffResult
except ImportError:
    Matcher = None
    DiffResult = None

try:
    from .syncer import Syncer
except ImportError:
    Syncer = None

try:
    from .notify import send_notification
except ImportError:
    send_notification = None

__all__ = [
    "Config",
    "LocalDatabase",
    "MovieRecord",
    "TraktClient",
    "DoubanClient",
    "Matcher",
    "DiffResult",
    "Syncer",
    "send_notification",
]
