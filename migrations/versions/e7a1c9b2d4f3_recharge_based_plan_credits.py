"""switch plans from monthly billing to recharge-based credits

Renames the monthly-flavoured names to recharge-flavoured ones, in the
column and inside every row's `limits` JSON:

    price_cents_monthly              -> price_cents
    max_call_minutes_per_month       -> max_call_minutes
    max_whatsapp_messages_per_month  -> max_whatsapp_messages
    max_tokens_per_month             -> max_tokens
    max_leads_per_month              -> max_leads

Values are untouched — a plan that included 1000 call minutes per month now
includes 1000 call minutes per recharge. Nothing resets on the calendar any
more; credits are consumed until the org buys the plan again (see
apps/api/core/usage.py).

`limits` is a `json` column, so the key surgery casts to `jsonb` (which has
the `-` / `||` operators) and back. Keys that aren't present on a given row
are skipped rather than written as null, so a plan whose limits only cover
seats/campaigns comes through unchanged.

Revision ID: e7a1c9b2d4f3
Revises: 22124d6b9b9d
Create Date: 2026-08-16 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = 'e7a1c9b2d4f3'
down_revision: Union[str, None] = '22124d6b9b9d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# old key -> new key
_LIMIT_KEY_RENAMES = {
    'max_call_minutes_per_month': 'max_call_minutes',
    'max_whatsapp_messages_per_month': 'max_whatsapp_messages',
    'max_tokens_per_month': 'max_tokens',
    'max_leads_per_month': 'max_leads',
}


def _rename_limit_keys(renames: dict[str, str]) -> None:
    """Emits one UPDATE per key so each is independently conditional on the
    key actually existing (`? `), rather than one statement that would have
    to reason about every combination of present/absent keys at once."""
    for old_key, new_key in renames.items():
        op.execute(
            f"""
            UPDATE plans
            SET limits = (
                (limits::jsonb - '{old_key}')
                || jsonb_build_object('{new_key}', limits::jsonb -> '{old_key}')
            )::json
            WHERE limits::jsonb ? '{old_key}'
            """
        )


def upgrade() -> None:
    op.alter_column('plans', 'price_cents_monthly', new_column_name='price_cents')
    _rename_limit_keys(_LIMIT_KEY_RENAMES)


def downgrade() -> None:
    _rename_limit_keys({new: old for old, new in _LIMIT_KEY_RENAMES.items()})
    op.alter_column('plans', 'price_cents', new_column_name='price_cents_monthly')
