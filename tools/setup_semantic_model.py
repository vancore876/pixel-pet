"""Explicitly download or validate the optional local semantic-recall model.

Running Jeffery never invokes this tool or downloads models automatically.
"""
from __future__ import annotations

import argparse
from importlib import metadata
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_FOLDER = "all-MiniLM-L6-v2"
MODEL_FILES = ("config.json", "modules.json", "model.safetensors", "1_Pooling/config.json")


def missing_model_files(path: Path) -> list[str]:
    return [name for name in MODEL_FILES if not (path / name).is_file()]


def default_model_path() -> Path:
    from config import data_directory
    return data_directory() / "models" / MODEL_FOLDER


def validate_model(path: Path) -> None:
    missing = missing_model_files(path)
    if missing:
        raise ValueError("The local model is incomplete: " + ", ".join(missing))
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(str(path), device="cpu", local_files_only=True,
                                trust_remote_code=False, model_kwargs={"use_safetensors": True})
    encoded = model.encode(["A local notebook memory."], normalize_embeddings=True)
    if len(encoded) != 1 or len(encoded[0]) != 384:
        raise ValueError("The model did not produce the expected 384-dimensional embedding.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, help="model directory; defaults to data/models/all-MiniLM-L6-v2")
    parser.add_argument("--check", action="store_true", help="validate local files without downloading anything")
    parser.add_argument("--revision", default="main", help="Hugging Face model revision or immutable commit ID")
    args = parser.parse_args(argv)
    for package in ("torch", "sentence-transformers"):
        try:
            metadata.version(package)
        except metadata.PackageNotFoundError:
            print(f"Missing {package}. Install the optional CPU dependencies described in README.md.",
                  file=sys.stderr)
            return 1
    try:
        destination = (args.output_dir or default_model_path()).expanduser().resolve()
        if not args.check:
            if destination.exists() and any(destination.iterdir()) and missing_model_files(destination):
                raise ValueError("The output directory contains incomplete or unrelated files. "
                                 "Choose an empty model directory or remove the failed model download first.")
            from huggingface_hub import snapshot_download
            print(f"Downloading {MODEL_ID} to {destination}…", flush=True)
            snapshot_download(repo_id=MODEL_ID, revision=args.revision, local_dir=str(destination),
                              allow_patterns=["*.json", "*.txt", "*.safetensors", "README.md", "LICENSE"])
        validate_model(destination)
    except Exception as error:
        print(f"Semantic model setup failed: {error}", file=sys.stderr)
        return 1
    print(f"Local semantic model ready: {destination}")
    print("Enable semantic notebook recall in Jeffery's settings and select this local directory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
