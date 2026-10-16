"""Private object storage adapters. Object keys are server-generated, never filenames."""

import asyncio
import re
from contextlib import closing
from pathlib import Path
from typing import Any, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings

KEY = re.compile(r"^[0-9a-f-]{36}/documents/[0-9a-f-]{36}/versions/[0-9a-f-]{36}$")


class StorageUnavailable(Exception):
    pass


class ObjectStore(Protocol):
    async def put(self, key: str, content: bytes, mime_type: str) -> None: ...
    async def get(self, key: str, expected_size: int) -> bytes: ...
    async def delete(self, key: str) -> None: ...
    async def healthy(self) -> bool: ...


def valid_key(key: str) -> None:
    if not KEY.fullmatch(key):
        raise StorageUnavailable("Invalid internal object key")


class LocalObjectStore:
    """Explicit development/test adapter; not an S3 emulator or production backend."""

    def __init__(self, root: Path):
        self.root = root.resolve()

    def path(self, key: str) -> Path:
        valid_key(key)
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise StorageUnavailable("Invalid internal object key")
        return path

    async def put(self, key: str, content: bytes, mime_type: str) -> None:
        def write() -> None:
            path = self.path(key)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with path.open("xb") as output:
                path.chmod(0o600)
                output.write(content)

        try:
            await asyncio.to_thread(write)
        except OSError as exc:
            raise StorageUnavailable from exc

    async def get(self, key: str, expected_size: int) -> bytes:
        def read() -> bytes:
            with self.path(key).open("rb") as stream:
                return stream.read(expected_size + 1)

        try:
            return await asyncio.to_thread(read)
        except OSError as exc:
            raise StorageUnavailable from exc

    async def delete(self, key: str) -> None:
        try:
            await asyncio.to_thread(self.path(key).unlink, missing_ok=True)
        except OSError as exc:
            raise StorageUnavailable from exc

    async def healthy(self) -> bool:
        try:
            await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True, mode=0o700)
            return self.root.is_dir()
        except OSError:
            return False


class S3ObjectStore:
    def __init__(self, settings: Settings):
        self.settings = settings

    def client(self) -> Any:
        if self.settings.s3_access_key is None or self.settings.s3_secret_key is None:
            raise StorageUnavailable("Object storage credentials not configured")
        # Explicit credentials prevent SDK instance-metadata discovery or ambient AWS credentials.
        return boto3.client(
            "s3",
            endpoint_url=self.settings.s3_endpoint,
            aws_access_key_id=self.settings.s3_access_key.get_secret_value(),
            aws_secret_access_key=self.settings.s3_secret_key.get_secret_value(),
            region_name=self.settings.s3_region,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=2,
                read_timeout=5,
                retries={"max_attempts": 1},
            ),
        )

    async def put(self, key: str, content: bytes, mime_type: str) -> None:
        valid_key(key)

        def write() -> None:
            with closing(self.client()) as client:
                client.put_object(
                    Bucket=self.settings.s3_bucket,
                    Key=key,
                    Body=content,
                    ContentType=mime_type,
                    IfNoneMatch="*",
                )

        try:
            await asyncio.to_thread(write)
        except (BotoCoreError, ClientError, OSError) as exc:
            raise StorageUnavailable from exc

    async def get(self, key: str, expected_size: int) -> bytes:
        valid_key(key)

        def read() -> bytes:
            with closing(self.client()) as client:
                body = client.get_object(Bucket=self.settings.s3_bucket, Key=key)["Body"]
                try:
                    return bytes(body.read(expected_size + 1))
                finally:
                    body.close()

        try:
            return await asyncio.to_thread(read)
        except (BotoCoreError, ClientError, OSError) as exc:
            raise StorageUnavailable from exc

    async def delete(self, key: str) -> None:
        valid_key(key)

        def remove() -> None:
            with closing(self.client()) as client:
                client.delete_object(Bucket=self.settings.s3_bucket, Key=key)

        try:
            await asyncio.to_thread(remove)
        except (BotoCoreError, ClientError, OSError) as exc:
            raise StorageUnavailable from exc

    async def healthy(self) -> bool:
        def check() -> None:
            with closing(self.client()) as client:
                client.head_bucket(Bucket=self.settings.s3_bucket)

        try:
            await asyncio.to_thread(check)
            return True
        except (StorageUnavailable, BotoCoreError, ClientError, OSError):
            return False


def create_store(settings: Settings) -> ObjectStore:
    if settings.storage_backend == "local":
        return LocalObjectStore(settings.local_storage_path)
    return S3ObjectStore(settings)
