from __future__ import annotations

"""Document object storage abstraction.

Local development stores objects under runtime/. Protected profiles require a
shared mounted store (or an injected implementation) so every worker can read
the same object URI.
"""

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
from tempfile import NamedTemporaryFile
from typing import Any, BinaryIO, Iterator, Protocol
from urllib.parse import urlparse

from agent_core.config import project_path
from agent_core.runtime.profile import RuntimeProfile, get_runtime_profile


class DocumentObjectStore(Protocol):
    backend_name: str
    def put(self, stream: BinaryIO, *, object_key: str, max_bytes: int) -> str: ...
    @contextmanager
    def materialize(self, object_uri: str) -> Iterator[Path]: ...


def _copy_limited(stream: BinaryIO, destination: Path, max_bytes: int) -> None:
    total = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("wb") as out:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("uploaded document exceeds size limit")
                out.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise


class FilesystemDocumentObjectStore:
    def __init__(self, root: Path, *, shared: bool) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.backend_name = "shared_filesystem" if shared else "local_filesystem"

    def put(self, stream: BinaryIO, *, object_key: str, max_bytes: int) -> str:
        relative = Path(object_key)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid document object key")
        destination = (self.root / relative).resolve()
        if self.root not in destination.parents:
            raise ValueError("document object key escaped storage root")
        _copy_limited(stream, destination, max_bytes)
        return destination.as_uri()

    @contextmanager
    def materialize(self, object_uri: str) -> Iterator[Path]:
        parsed = urlparse(object_uri)
        path = Path(parsed.path if parsed.scheme == "file" else object_uri).resolve()
        if self.root not in path.parents or not path.is_file():
            raise FileNotFoundError("document object is unavailable in configured store")
        yield path


class S3DocumentObjectStore:
    """Optional S3-compatible implementation; install boto3 in deployments using it."""

    backend_name = "s3"

    def __init__(self, *, bucket: str, prefix: str = "", endpoint_url: str | None = None, region: str | None = None) -> None:
        try:
            import boto3  # type: ignore
        except Exception as exc:
            raise RuntimeError("DOCUMENT_OBJECT_STORE_BACKEND=s3 requires boto3") from exc
        if not bucket:
            raise RuntimeError("DOCUMENT_S3_BUCKET is required")
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = boto3.client("s3", endpoint_url=endpoint_url or None, region_name=region or None)

    def _key(self, object_key: str) -> str:
        relative = Path(object_key)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid document object key")
        key = relative.as_posix().lstrip("/")
        return f"{self.prefix}/{key}" if self.prefix else key

    def put(self, stream: BinaryIO, *, object_key: str, max_bytes: int) -> str:
        key = self._key(object_key)
        with NamedTemporaryFile(prefix="document-upload-", delete=False) as temp:
            temp_path = Path(temp.name)
        try:
            _copy_limited(stream, temp_path, max_bytes)
            self.client.upload_file(str(temp_path), self.bucket, key)
        finally:
            temp_path.unlink(missing_ok=True)
        return f"s3://{self.bucket}/{key}"

    @contextmanager
    def materialize(self, object_uri: str) -> Iterator[Path]:
        parsed = urlparse(object_uri)
        if parsed.scheme != "s3" or parsed.netloc != self.bucket:
            raise ValueError("object URI does not belong to configured S3 bucket")
        key = parsed.path.lstrip("/")
        with NamedTemporaryFile(prefix="document-index-", suffix=Path(key).suffix, delete=False) as temp:
            path = Path(temp.name)
        try:
            self.client.download_file(self.bucket, key, str(path))
            yield path
        finally:
            path.unlink(missing_ok=True)


def build_document_object_store() -> DocumentObjectStore:
    profile = get_runtime_profile(strict=True)
    default_backend = "local_filesystem" if profile is RuntimeProfile.LOCAL else "shared_filesystem"
    backend = (os.getenv("DOCUMENT_OBJECT_STORE_BACKEND") or default_backend).strip().lower()
    if profile in {RuntimeProfile.PREPROD, RuntimeProfile.PRODUCTION} and backend in {"local", "local_filesystem"}:
        raise RuntimeError("DOCUMENT_OBJECT_STORE_BACKEND must be shared in preprod/production")
    if backend in {"local", "local_filesystem"}:
        return FilesystemDocumentObjectStore(project_path(os.getenv("DOCUMENT_OBJECT_STORE_ROOT"), "runtime/document-objects"), shared=False)
    if backend in {"shared", "shared_filesystem"}:
        raw = (os.getenv("DOCUMENT_OBJECT_STORE_ROOT") or "").strip()
        if not raw:
            raise RuntimeError("DOCUMENT_OBJECT_STORE_ROOT is required for shared_filesystem")
        root = Path(raw)
        if not root.is_absolute():
            raise RuntimeError("DOCUMENT_OBJECT_STORE_ROOT must be an absolute shared mount path")
        return FilesystemDocumentObjectStore(root, shared=True)
    if backend == "s3":
        return S3DocumentObjectStore(
            bucket=(os.getenv("DOCUMENT_S3_BUCKET") or "").strip(),
            prefix=(os.getenv("DOCUMENT_S3_PREFIX") or "").strip(),
            endpoint_url=(os.getenv("DOCUMENT_S3_ENDPOINT_URL") or "").strip() or None,
            region=(os.getenv("DOCUMENT_S3_REGION") or "").strip() or None,
        )
    raise RuntimeError("DOCUMENT_OBJECT_STORE_BACKEND must be local_filesystem, shared_filesystem, or s3")
