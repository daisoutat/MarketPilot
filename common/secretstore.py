"""Thin, synchronous Secrets Manager reader (used at startup / in workers)."""
from __future__ import annotations

import json
import os

import boto3


def get_secret_dict(arn: str, region: str | None = None) -> dict:
    """Fetch a Secrets Manager SecretString and parse it as JSON."""
    region = region or os.environ.get("AWS_REGION", "us-east-1")
    client = boto3.client("secretsmanager", region_name=region)
    value = client.get_secret_value(SecretId=arn)["SecretString"]
    return json.loads(value)


def get_secret(arn: str, region: str | None = None) -> str:
    """Fetch a plain SecretString (no JSON parsing)."""
    region = region or os.environ.get("AWS_REGION", "us-east-1")
    client = boto3.client("secretsmanager", region_name=region)
    return client.get_secret_value(SecretId=arn)["SecretString"]