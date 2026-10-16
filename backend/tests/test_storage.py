import asyncio
import io
from unittest.mock import patch
from uuid import uuid4

from botocore.response import StreamingBody
from botocore.stub import Stubber
from pydantic import SecretStr

from app.core.config import Settings
from app.documents.storage import S3ObjectStore


def test_s3_sdk_contract_without_network() -> None:
    store = S3ObjectStore(
        Settings(s3_access_key=SecretStr("test-only"), s3_secret_key=SecretStr("test-only-secret"))
    )
    client = store.client()
    key = f"{uuid4()}/documents/{uuid4()}/versions/{uuid4()}"
    bucket = store.settings.s3_bucket
    with Stubber(client) as stub, patch.object(store, "client", return_value=client):
        stub.add_response(
            "put_object",
            {},
            {
                "Bucket": bucket,
                "Key": key,
                "Body": b"lease",
                "ContentType": "text/plain",
                "IfNoneMatch": "*",
            },
        )
        stub.add_response(
            "get_object",
            {"Body": StreamingBody(io.BytesIO(b"lease"), 5)},
            {"Bucket": bucket, "Key": key},
        )
        stub.add_response("delete_object", {}, {"Bucket": bucket, "Key": key})
        stub.add_response("head_bucket", {}, {"Bucket": bucket})
        asyncio.run(store.put(key, b"lease", "text/plain"))
        assert asyncio.run(store.get(key, 5)) == b"lease"
        asyncio.run(store.delete(key))
        assert asyncio.run(store.healthy()) is True
        stub.assert_no_pending_responses()


def test_storage_failure_is_not_a_fake_healthy_result() -> None:
    assert (
        asyncio.run(S3ObjectStore(Settings(s3_access_key=None, s3_secret_key=None)).healthy())
        is False
    )
