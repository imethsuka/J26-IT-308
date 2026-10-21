# Contributing

## Scope

This project is scoped to **Member 1: adaptive desktop context acquisition and
importance learning** only.

In scope:
- Acquiring desktop context (Win32 hooks, screen capture) in `csharp-collector/`.
- Feature extraction, importance classification, online adaptation, local storage
  and evaluation in `python-worker/`.
- IPC between the collector and the worker.

Out of scope (handled by other members, do **not** add here):
- **Privacy / tokenization logic**: no PII redaction, masking, anonymization or tokenization.
- **Retrieval logic**: no search, indexing-for-retrieval, RAG or query answering.

If a change needs either of these, define an interface boundary and leave the
implementation to the owning component.

## Conventions
- Python: 3.11, dependencies pinned in `python-worker/requirements.txt`, use a venv (`.venv/`).
- C#: .NET 8, nullable reference types enabled.
- Do not commit model weights, checkpoints, captured screenshots or local data;
  `.gitignore` covers these.
