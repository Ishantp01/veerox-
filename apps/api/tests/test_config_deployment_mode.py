"""apps/api/config.py's `_validate_deployment_mode_requirements` — one
shared schema, mode-conditional validation. A client deployment must have
what it needs to reach and authenticate to the owner (`LICENSE_API_URL`,
`LICENSE_API_TOKEN`); the owner needs nothing extra since it validates
itself via the platform-owner exemption, not a client token."""

from __future__ import annotations

import pytest

from apps.api.config import Settings


def test_owner_mode_needs_nothing_extra() -> None:
    Settings(admin_token="t")  # deployment_mode defaults to "owner"


@pytest.mark.parametrize("missing_field", ["license_api_url", "license_api_token"])
def test_client_mode_requires_its_own_fields(missing_field: str) -> None:
    fields = {"license_api_url": "https://owner.example", "license_api_token": "tok"}
    fields[missing_field] = None
    with pytest.raises(ValueError, match="DEPLOYMENT_MODE=client requires"):
        Settings(admin_token="t", deployment_mode="client", **fields)


def test_client_mode_with_required_fields_succeeds() -> None:
    Settings(
        admin_token="t",
        deployment_mode="client",
        license_api_url="https://owner.example",
        license_api_token="tok",
    )


def test_unknown_deployment_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="DEPLOYMENT_MODE must be one of"):
        Settings(admin_token="t", deployment_mode="nonsense")
