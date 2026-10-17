import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def search_index(
    chunks: list[dict[str, Any]],
    query_embedding: list[float],
    top_k: int = 5,
) -> list[tuple[float, dict[str, Any]]]:
    scored = [
        (cosine_similarity(query_embedding, chunk.get("embedding", [])), chunk)
        for chunk in chunks
    ]
    return sorted(scored, key=lambda item: item[0], reverse=True)[:top_k]


def load_index(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as index_file:
            index = json.load(index_file)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(index, dict) or not isinstance(index.get("chunks"), list):
        return None
    return index


def save_index(path: Path, index: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            suffix=".tmp",
            delete=False,
        ) as index_file:
            json.dump(index, index_file, ensure_ascii=True)
            temporary_path = Path(index_file.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
