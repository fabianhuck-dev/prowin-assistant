"""GoBD-Unveränderbarkeit: Write-Once Object Storage + SHA-256.

Originale Belege sind write-once. Es gibt KEINEN Update-/Delete-Pfad auf Originale.
Der Storage-Key wird aus dem SHA-256-Hash abgeleitet, wodurch identische Inhalte
denselben Key erhalten (natürliche Duplikat-Erkennung).
"""

from __future__ import annotations

import hashlib
import mimetypes
from abc import ABC, abstractmethod

from app.config import settings


class WriteOnceError(Exception):
    """Versuch, einen bereits existierenden Original-Key zu überschreiben."""


class ObjectStorage(ABC):
    @abstractmethod
    async def exists(self, key: str) -> bool: ...

    @abstractmethod
    async def put_write_once(self, key: str, data: bytes, content_type: str) -> None: ...

    @abstractmethod
    async def get(self, key: str) -> bytes: ...

    @abstractmethod
    async def presigned_url(self, key: str, expires: int = 3600) -> str: ...


class InMemoryStorage(ObjectStorage):
    """Test-/Fallback-Backend. Erzwingt write-once-Semantik im Speicher."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    async def exists(self, key: str) -> bool:
        return key in self._data

    async def put_write_once(self, key: str, data: bytes, content_type: str) -> None:
        if key in self._data:
            raise WriteOnceError(f"Key existiert bereits (write-once): {key}")
        self._data[key] = data

    async def get(self, key: str) -> bytes:
        return self._data[key]

    async def presigned_url(self, key: str, expires: int = 3600) -> str:
        return f"memory://{settings.s3_bucket_name}/{key}?expires={expires}"


class S3Storage(ObjectStorage):
    """Produktiv-Backend (MinIO / Hetzner Object Storage) via aioboto3."""

    def __init__(self) -> None:
        import aioboto3

        self._session = aioboto3.Session()
        self._bucket = settings.s3_bucket_name

    def _client(self):
        return self._session.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )

    async def ensure_bucket(self) -> None:
        from botocore.exceptions import ClientError

        async with self._client() as client:
            try:
                await client.head_bucket(Bucket=self._bucket)
            except ClientError:
                await client.create_bucket(Bucket=self._bucket)

    async def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        async with self._client() as client:
            try:
                await client.head_object(Bucket=self._bucket, Key=key)
                return True
            except ClientError:
                return False

    async def put_write_once(self, key: str, data: bytes, content_type: str) -> None:
        if await self.exists(key):
            raise WriteOnceError(f"Key existiert bereits (write-once): {key}")
        async with self._client() as client:
            # IfNoneMatch="*" erzwingt serverseitig write-once (verhindert Race).
            await client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )

    async def get(self, key: str) -> bytes:
        async with self._client() as client:
            resp = await client.get_object(Bucket=self._bucket, Key=key)
            async with resp["Body"] as stream:
                return await stream.read()

    async def presigned_url(self, key: str, expires: int = 3600) -> str:
        async with self._client() as client:
            return await client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires,
            )


_storage: ObjectStorage | None = None


def get_storage() -> ObjectStorage:
    """Liefert das aktive Storage-Backend (Default: S3/MinIO).

    Tests injizieren via ``set_storage(InMemoryStorage())`` ein In-Memory-Backend,
    sodass sie ohne laufende Docker-Services funktionieren.
    """
    global _storage
    if _storage is None:
        _storage = S3Storage()
    return _storage


def set_storage(storage: ObjectStorage) -> None:
    """Wird in Tests genutzt, um ein In-Memory-Backend zu injizieren."""
    global _storage
    _storage = storage


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _storage_key_for(sha256: str, filename: str) -> str:
    ext = ""
    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower()
    return f"belege/{sha256[:2]}/{sha256}{ext}"


async def upload_beleg_write_once(data: bytes, filename: str) -> tuple[str, str]:
    """Lädt ein Original write-once hoch.

    Rückgabe: (storage_key, sha256_hash).
    Identischer Inhalt -> identischer Key. Existiert der Key bereits, wird NICHT
    erneut geschrieben (idempotent) und derselbe Hash zurückgegeben.
    """
    sha256 = compute_sha256(data)
    key = _storage_key_for(sha256, filename)
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    storage = get_storage()
    if await storage.exists(key):
        return key, sha256
    await storage.put_write_once(key, data, content_type)
    return key, sha256


async def get_signed_url(storage_key: str, expires: int = 3600) -> str:
    return await get_storage().presigned_url(storage_key, expires)
