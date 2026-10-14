from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def canonical(value) -> bytes:
    return (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
        + "\n"
    ).encode()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(canonical(value))
    os.replace(temp, path)


def checksum_tree(root: Path) -> None:
    rows = [
        f"{digest(p)}  {p.relative_to(root).as_posix()}"
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name != "SHA256SUMS"
    ]
    (root / "SHA256SUMS").write_text("\n".join(rows) + "\n")
