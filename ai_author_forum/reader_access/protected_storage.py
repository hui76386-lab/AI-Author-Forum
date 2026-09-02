"""Private immutable storage adapters for protected PDF releases."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation


FILESYSTEM_OBJECT_MODE = 0o644


@dataclass(frozen=True)
class ProtectedObjectMetadata:
    key: str
    byte_size: int
    sha256: str


def validate_object_key(value):
    raw = str(value or "")
    if not raw or "\\" in raw:
        raise SuspiciousFileOperation("Invalid protected object key.")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        raise SuspiciousFileOperation("Invalid protected object key.")
    if tuple(path.parts[:2]) != ("protected", "releases"):
        raise SuspiciousFileOperation("Protected objects must use a release prefix.")
    return path.as_posix()


def object_sha256(data):
    return hashlib.sha256(data).hexdigest()


class FileSystemProtectedStorage:
    backend_name = "filesystem"

    def __init__(self, root=None):
        self.root = Path(root or settings.READER_PRIVATE_STORAGE_ROOT).resolve()

    def _path(self, key):
        key = validate_object_key(key)
        candidate = (self.root / Path(*PurePosixPath(key).parts)).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise SuspiciousFileOperation("Protected path escaped its root.") from exc
        return candidate

    def ensure_readable(self, key):
        target = self._path(key)
        target.chmod(FILESYSTEM_OBJECT_MODE)

    def put_bytes(self, key, data):
        key = validate_object_key(key)
        data = bytes(data)
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            current = self.metadata(key)
            if current.byte_size == len(data) and current.sha256 == object_sha256(data):
                self.ensure_readable(key)
                return current
            raise FileExistsError("Immutable protected object already exists.")
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=target.parent, prefix=".protected-", delete=False
            ) as stream:
                temporary_path = Path(stream.name)
                os.chmod(temporary_path, 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary_path, FILESYSTEM_OBJECT_MODE)
            os.link(temporary_path, target)
            directory_fd = os.open(target.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return ProtectedObjectMetadata(key, len(data), object_sha256(data))

    def metadata(self, key):
        key = validate_object_key(key)
        target = self._path(key)
        digest = hashlib.sha256()
        byte_size = 0
        with target.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                byte_size += len(chunk)
                digest.update(chunk)
        return ProtectedObjectMetadata(key, byte_size, digest.hexdigest())

    def exists(self, key):
        return self._path(key).is_file()


class S3ProtectedStorage:
    backend_name = "s3"

    def __init__(self, client=None, bucket=None):
        self.bucket = bucket or settings.READER_S3_BUCKET
        if not self.bucket:
            raise ImproperlyConfigured("READER_S3_BUCKET is required.")
        if client is None:
            import boto3

            client = boto3.client(
                "s3",
                endpoint_url=settings.READER_S3_ENDPOINT_URL or None,
                region_name=settings.READER_S3_REGION or None,
                aws_access_key_id=settings.READER_S3_ACCESS_KEY_ID or None,
                aws_secret_access_key=settings.READER_S3_SECRET_ACCESS_KEY or None,
            )
        self.client = client

    def put_bytes(self, key, data):
        key = validate_object_key(key)
        data = bytes(data)
        digest = object_sha256(data)
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=(
                "application/pdf" if key.endswith(".pdf") else "application/json"
            ),
            Metadata={"sha256": digest},
            IfNoneMatch="*",
        )
        return ProtectedObjectMetadata(key, len(data), digest)

    def metadata(self, key):
        key = validate_object_key(key)
        response = self.client.head_object(Bucket=self.bucket, Key=key)
        digest = (response.get("Metadata") or {}).get("sha256", "")
        return ProtectedObjectMetadata(key, int(response["ContentLength"]), digest)

    def exists(self, key):
        try:
            self.metadata(key)
        except Exception:  # noqa: BLE001 - providers expose different not-found errors
            return False
        return True

    def presigned_download(self, key, *, expires_seconds, filename):
        key = validate_object_key(key)
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentType": "application/pdf",
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
            },
            ExpiresIn=int(expires_seconds),
        )


def get_protected_storage():
    backend = settings.READER_PRIVATE_STORAGE_BACKEND
    if backend == "filesystem":
        return FileSystemProtectedStorage()
    if backend == "s3":
        return S3ProtectedStorage()
    raise ImproperlyConfigured("Unsupported READER_PRIVATE_STORAGE_BACKEND.")
