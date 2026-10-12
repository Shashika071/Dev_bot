"""Immutable run manifests and artifact checksums."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dataset_manifest_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_run_manifest(run_dir: str, metadata: dict[str, Any]) -> str:
    os.makedirs(run_dir, exist_ok=True)
    path = os.path.join(run_dir, "manifest.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, default=str)
    return path


def checksum_paths(paths: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, path in paths.items():
        if path and os.path.isfile(path):
            out[name] = sha256_file(path)
    return out


def verify_artifact_checksums(metadata: dict | None) -> None:
    """Fail closed when declared checksums do not match the files.

    Metadata written before checksums existed is allowed through. A declared
    checksum for a missing or different file is not.
    """
    if not metadata:
        return
    checksums = metadata.get("artifact_sha256") or {}
    if not checksums:
        return
    paths = dict(metadata.get("paths") or {})
    for name, expected in checksums.items():
        path = paths.get(name)
        if not path or not os.path.isfile(path):
            raise FileNotFoundError(f"Missing artifact for checksum '{name}': {path}")
        actual = sha256_file(path)
        if actual != expected:
            raise ValueError(f"Checksum mismatch for {name}")
