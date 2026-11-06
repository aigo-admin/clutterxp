"""History: operations journal, SQLite storage, undo, and crash recovery."""

from clutterxp.history.db import (
    CURRENT_SCHEMA_VERSION,
    connect,
    handle_corrupt_db,
    integrity_check,
    run_migrations,
)
from clutterxp.history.pathcodec import decode, encode
from clutterxp.history.service import HistoryService, op_to_dict

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "HistoryService",
    "connect",
    "decode",
    "encode",
    "handle_corrupt_db",
    "integrity_check",
    "op_to_dict",
    "run_migrations",
]
