"""Explicit development/CI bucket provisioning; the API never auto-creates buckets."""

import time
from contextlib import closing
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings
from app.documents.storage import S3ObjectStore, StorageUnavailable


def main() -> None:
    settings = Settings()
    store = S3ObjectStore(settings)
    for attempt in range(30):
        try:
            with closing(store.client()) as client:
                client.list_buckets()
                try:
                    client.head_bucket(Bucket=settings.s3_bucket)
                except ClientError as exc:
                    if str(exc.response.get("Error", {}).get("Code")) not in {
                        "404",
                        "NoSuchBucket",
                        "NotFound",
                    }:
                        raise
                    args: dict[str, Any] = {"Bucket": settings.s3_bucket}
                    if settings.s3_region != "us-east-1":
                        args["CreateBucketConfiguration"] = {
                            "LocationConstraint": settings.s3_region
                        }
                    client.create_bucket(**args)
            print("Document bucket is available; verify its private access policy before use.")
            return
        except (BotoCoreError, ClientError, StorageUnavailable):
            if attempt == 29:
                raise SystemExit(
                    "Storage provisioning failed; check endpoint, permissions and credentials."
                ) from None
            time.sleep(1)


if __name__ == "__main__":
    main()
