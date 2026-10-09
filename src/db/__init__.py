"""SQLite storage, with one connection shared by focused repositories."""

from .database import Database

__all__ = ["Database"]
