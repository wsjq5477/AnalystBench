"""Content-addressed local storage for immutable text and JSON artifacts."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ContentRef:
    content_hash: str
    media_type: str
    size_bytes: int
    storage_path: str


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


class ContentStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put_text(self, text: str, media_type: str = "text/plain; charset=utf-8") -> ContentRef:
        return self.put_bytes(text.encode("utf-8"), media_type)

    def put_json(self, value: object) -> ContentRef:
        return self.put_text(canonical_json(value), "application/json")

    def put_bytes(self, data: bytes, media_type: str) -> ContentRef:
        digest = content_hash(data)
        relative_path = Path(digest.removeprefix("sha256:")[:2]) / digest.removeprefix("sha256:")
        destination = self.root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            temporary = destination.with_suffix(".tmp")
            temporary.write_bytes(data)
            temporary.replace(destination)
        return ContentRef(
            content_hash=digest,
            media_type=media_type,
            size_bytes=len(data),
            storage_path=relative_path.as_posix(),
        )

    def read_text(self, digest: str) -> str:
        path = self.root / digest.removeprefix("sha256:")[:2] / digest.removeprefix("sha256:")
        return path.read_text(encoding="utf-8")
