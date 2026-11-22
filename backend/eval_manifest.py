"""Identify the code and data inputs of both mock and provider evaluations."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

from ingest import MANIFEST, ROOT


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def relative(path: Path) -> str:
    path = path.resolve()
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name


def commit() -> str | None:
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
                                      stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return sha if re.fullmatch("[0-9a-f]{40,64}", sha) else None


def manifest(*, model: str, prompt_version: str, effective_prompts: dict, cases: Path, dataset_version: str,
             corpus: dict, index: dict, retrieval: dict) -> dict:
    prompt_files = sorted((ROOT / "prompts").rglob("*.txt"))
    source_files = [*sorted((ROOT / "backend" / "app").rglob("*.py")),
                    *sorted((ROOT / "backend").glob("*eval*.py")), ROOT / "ingest.py", ROOT / "retrieve.py"]
    inputs = {
        "model": model,
        "prompts": {
            "version": prompt_version,
            "effective": {name: digest(content) for name, content in effective_prompts.items()},
            "files": {relative(path): digest(path.read_text(encoding="utf-8")) for path in prompt_files},
        },
        "corpus": {
            "version": "orbita-v1" if MANIFEST == ROOT / "data" / "manifest.json" else "custom-v1",
            "manifest": relative(MANIFEST),
            "sha256": digest(canonical({
                "manifest": json.loads(MANIFEST.read_text(encoding="utf-8-sig")),
                "documents": {doc_id: doc.text for doc_id, doc in corpus.items()},
            })),
        },
        "index": {**index, "sha256": digest(canonical(index))},
        "retrieval": retrieval,
        "dataset": {"version": dataset_version, "path": relative(cases),
                    "sha256": digest(canonical(json.loads(cases.read_text(encoding="utf-8-sig"))))},
        "code_sha256": digest(canonical({relative(path): digest(path.read_text(encoding="utf-8")) for path in source_files})),
    }
    return {"manifest_version": "1", "commit": commit(), "inputs_sha256": digest(canonical(inputs)), **inputs}
