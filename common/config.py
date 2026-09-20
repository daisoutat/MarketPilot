"""Application settings shared by API and workers.

All environment variables use the `MP_` prefix, e.g. `MP_DATABASE_URL`.
"""
import json
import os
from functools import lru_cache

import boto3
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MP_", env_file=".env", extra="ignore")

    app_name: str = "MarketPilot"
    app_env: str = "dev"  # dev | prod

    # Authentication: "cognito" validates real JWTs; "dev" bypasses for local work.
    auth_mode: str = "dev"
    cognito_region: str = "us-east-1"
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""  # audience for access tokens
    dev_user: str = "demo@radisson.example|Demo Analyst|analyst"

    # Database (SQLAlchemy async URL, asyncpg driver). Prefer the Secrets
    # Manager ARN on ECS/Lambda: the DSN is built at startup from the secret.
    database_url: str = ""
    db_secret_arn: str = ""

    # SP-API (key-based / new authorization model). On ECS/Lambda these come
    # as `env:` refs resolved by the worker; locally they can be set directly.
    sp_client_id: str = ""
    sp_client_secret: str = ""
    sp_role_arn: str = ""
    sp_region: str = "us-east-1"      # SigV4 signing region for SP-API NA
    sp_base_url: str = "https://sellingpartnerapi-na.amazon.com"

    # PA-API 5.0 (market research) — Associates account (SigV4 IAM keys + tag).
    pa_access_key: str = ""
    pa_secret_key: str = ""
    pa_partner_tag: str = ""
    pa_region: str = "us-east-1"
    # Comma-separated search seeds for research-target discovery.
    research_keywords: str = ""

    # FX (view-time normalization): Frankfurter/ECB by default.
    fx_base_url: str = "https://api.frankfurter.app"
    fx_ttl_seconds: int = 1800

    cors_origins: str = "*"


def resolve_url_from_secret_arn(arn: str) -> str:
    """Build an asyncpg DSN from the Aurora-generated secret JSON.

    Requires `secretsmanager:GetSecretValue` on the ARN (granted by CDK to
    task/execution roles).
    """
    client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    value = json.loads(client.get_secret_value(SecretId=arn)["SecretString"])
    return "postgresql+asyncpg://{u}:{p}@{h}:{port}/{db}".format(
        u=value["username"], p=value["password"], h=value["host"], port=value["port"],
        db=value.get("dbname", "postgres")
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()