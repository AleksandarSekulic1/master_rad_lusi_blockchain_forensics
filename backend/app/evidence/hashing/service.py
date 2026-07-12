from __future__ import annotations

import hashlib
from pathlib import Path


def calculate_sha256(file_path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    path = Path(file_path)
    digest = hashlib.sha256()

    with path.open('rb') as file_handle:
        for chunk in iter(lambda: file_handle.read(chunk_size), b''):
            digest.update(chunk)

    return digest.hexdigest()
