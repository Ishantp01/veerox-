from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base


class OrgPhoneNumber(Base):
    """One dedicated Plivo, Twilio, or WhatsApp number owned by an org — an
    org may have several per provider (see routers/billing.py::update_org and
    routers/auth.py::provision_org). Replaces the old single-column
    Org.plivo_phone_number/twilio_phone_number (see migration
    <rev>_add_org_phone_numbers_table) and, later, Org.whatsapp_phone_number_id
    (see migration <rev>_add_whatsapp_multi_number_and_script).

    Outbound calls round-robin across every number an org has per provider
    (see channels/voice/org_numbers.py::get_rotating_numbers), in `position`
    order — not `created_at`, which is unreliable for this: multiple rows
    written in the same replace_org_phone_numbers call (the normal case, one
    PUT from the settings page) can land in the same DB transaction and get
    an identical timestamp. `position` is that provider's 0-based index in
    the submission order instead, set by replace_org_phone_numbers. WhatsApp
    rows don't rotate (channels/whatsapp/adapter.py sends from whichever
    number a message came in on, or a campaign's pinned choice) but still use
    `position` for stable settings-page ordering.

    Exactly one row per (org_id, provider) is expected to carry
    is_default=True — for plivo/twilio, purely a "Primary" label for the
    settings UI (see channels/voice/org_numbers.py::get_default_numbers) and
    no longer affects which number a call goes out from, the rest are
    reachable for inbound routing only
    (channels/voice/webhook.py::_resolve_org_by_number). For whatsapp, it's
    load-bearing: the fallback number for any send with no more specific
    choice (see workers/whatsapp_dispatcher.py and routers/admin.py's
    outbound-message route). Enforced in application code, not a DB
    constraint, since a partial unique index doesn't translate to the SQLite
    backend the test suite runs against.
    """

    __tablename__ = "org_phone_numbers"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(10), nullable=False)  # "plivo" | "twilio" | "whatsapp"
    # Digits only, no "+" for plivo/twilio (same convention as the columns
    # those replaced — see channels/voice/webhook.py::_normalize_phone). For
    # "whatsapp", this holds Meta's `phone_number_id` instead (an opaque
    # numeric id from the Meta dashboard, not the E.164 number itself) — see
    # channels/whatsapp/adapter.py and channels/whatsapp/client.py.
    phone_number: Mapped[str] = mapped_column(String(32), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # This provider's 0-based rotation order — see class docstring.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Scoped to plivo/twilio only — a WhatsApp phone_number_id is allowed to
    # repeat across orgs for now (see routers/billing.py::update_org, the
    # duplicate-check there is likewise skipped for provider="whatsapp").
    __table_args__ = (
        Index(
            "uq_org_phone_numbers_provider_number",
            "provider",
            "phone_number",
            unique=True,
            postgresql_where=text("provider != 'whatsapp'"),
            sqlite_where=text("provider != 'whatsapp'"),
        ),
    )
