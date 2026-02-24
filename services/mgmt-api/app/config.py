from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VW_", case_sensitive=False)

    service_name: str = "vw-mgmt-api"

    # DB
    db_dsn: str = Field(default="postgresql://vw:vw@postgres:5432/vw")
    db_min_size: int = 1
    db_max_size: int = 10

    # OIDC/JWT validation (offline)
    oidc_issuer: str = Field(default="", description="Expected issuer (optional)")
    oidc_audience: str = Field(default="", description="Expected audience (optional)")
    oidc_client_id: str = Field(default="vw", description="Client id for resource_access roles")
    oidc_public_key_pem: str = Field(default="", description="PEM public key/cert for RS256 verification")
    oidc_jwks_path: str = Field(default="", description="Optional path to offline JWKS JSON file")

    # Inter-service URLs
    policy_url: str = "http://vw-policy:8001"
    health_url: str = "http://vw-health:8003"
    audit_url: str = "http://vw-audit:8002"
    config_url: str = Field(default="http://vw-config:8006", description="vw-config base URL for reconciliation")

    # Stream token minting (HS256)
    stream_token_secret: str = Field(default="change-me")
    stream_token_ttl_seconds: int = 300

    # Bundle import verification (optional HMAC)
    bundle_hmac_secret: str = Field(default="")

    # Audit chain
    audit_chain_id: str = Field(default="mgmt-api")


settings = Settings()
