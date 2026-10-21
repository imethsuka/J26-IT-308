"""Storage.

Responsibility: local persistence of context records (features + embedding + decision + label)
and model version history, in SQLite.

- ``schema`` / ``database`` — DDL, ordered migrations, connection, embedding/time encoders.
- ``context_store`` — insert/query for context records and model versions, including the
  label-construction query (unlabelled records older than N hours).
- ``migrate`` — CLI to apply migrations.
- ``report_storage`` — CLI reporting DB size and row counts (the storage-overhead metric).
"""

from .context_store import (  # noqa: F401
    ContextRecord,
    ModelVersion,
    count_context_records,
    get_active_model_version,
    get_context_record,
    get_model_version,
    insert_context_record,
    insert_model_version,
    list_context_records,
    list_model_versions,
    query_unlabelled_older_than,
    set_active_model_version,
    set_label,
)
from .database import connect, migrate  # noqa: F401
