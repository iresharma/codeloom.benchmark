"""Export only credential-free, sealed evidence; never copy ledger or worker environment."""

from __future__ import annotations
import gzip
import json
import shutil
from pathlib import Path
from .artifacts import checksum_tree, write_json


def export_traces(root: Path, destination: Path):
    for trial in sorted((root / "trials").glob("*")):
        if not (trial / "result.json").exists():
            if (trial / "attempt.json").exists():
                from .reporting import unsealed_attempt

                target = destination / "trials" / trial.name
                target.mkdir(parents=True, exist_ok=True)
                write_json(target / "result.json", unsealed_attempt(trial))
            # Active/unsealed logs are withheld until token revocation and sealing are verified.
            continue
        target = destination / "trials" / trial.name
        target.mkdir(parents=True, exist_ok=True)
        for name in (
            "result.json",
            "attempt.json",
            "verification.json",
            "native-trace.json",
            "engine-events.jsonl",
            "patch.diff",
            "broker.jsonl",
            "controller.log",
            "engine-process.log",
        ):
            source = trial / name
            if source.exists():
                text = source.read_text()
                if "sk-or-v1-" in text:
                    raise ValueError(
                        "Credential marker in export; refusing publication"
                    )
                if name.endswith(".json"):
                    write_json(target / name, json.loads(text))
                else:
                    (target / name).write_text(text)
        if (trial / "broker.jsonl").exists():
            for line in (trial / "broker.jsonl").read_text().splitlines():
                row = json.loads(line)
                for key in ("request_blob", "response_blob"):
                    if row.get(key):
                        p = Path(row[key])
                        if p.is_absolute() or ".." in p.parts:
                            raise ValueError("Unsafe artifact reference")
                        blob = root / p
                        if b"sk-or-v1-" in gzip.decompress(blob.read_bytes()):
                            raise ValueError(
                                "Credential marker in compressed evidence; refusing publication"
                            )
                        (destination / p).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(blob, destination / p)
    checksum_tree(destination)
