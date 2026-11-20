"""Replace old ``[template:name]`` chat rows with the text the customer received.

Before sends stored their rendered text, a template send left only a marker in
``messages.content``, so Chat showed "Hi …, your appointment is … at …". The
values are still recoverable:

* reminders  -> the sent ``FollowUpTask`` that fired them (exact params), and
* confirmations -> the ``Appointment`` booked seconds before the message
  (name / date / time, built the same way ``book_appointment`` builds them).

A row with no trustworthy source is left untouched. Dry run by default:

    PYTHONPATH=. python scripts/backfill_template_text.py
    PYTHONPATH=. python scripts/backfill_template_text.py --apply

Reads DATABASE_URL via the app's settings (.env.local, else .env) — check the
host it prints before using --apply. Safe to re-run.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select

from apps.api.core.tools import DEFAULT_BOOKING_TIMEZONE, _format_display_time
from apps.api.core.whatsapp_render import LEGACY_TEMPLATE_MARKER, render_template_message
from apps.api.db.models import Appointment, FollowUpTask, Lead, Message, User, WhatsAppTemplate
from apps.api.db.session import AsyncSessionLocal, engine

_TASK_WINDOW = timedelta(minutes=5)
_BOOKING_WINDOW = timedelta(minutes=10)


def _usable(params: list | None) -> bool:
    return bool(params) and not any("{{" in str(p) for p in params)


async def _task_params(db, message: Message, name: str):
    rows = (
        await db.execute(
            select(FollowUpTask)
            .join(Lead, Lead.id == FollowUpTask.lead_id)
            .where(
                Lead.user_id == message.user_id,
                FollowUpTask.template_name == name,
                FollowUpTask.status == "sent",
                FollowUpTask.sent_at.is_not(None),
                FollowUpTask.sent_at >= message.created_at - _TASK_WINDOW,
                FollowUpTask.sent_at <= message.created_at + _TASK_WINDOW,
            )
        )
    ).scalars().all()
    rows = [r for r in rows if _usable(r.template_params)]
    if not rows:
        return None
    best = min(rows, key=lambda r: abs(r.sent_at - message.created_at))
    return best.template_params, best.template_header_params


async def _booking_params(db, message: Message, template: WhatsAppTemplate, user: User | None):
    """Name/date/time of the appointment booked just before a confirmation."""
    appt = (
        await db.execute(
            select(Appointment, Lead.name)
            .join(Lead, Lead.id == Appointment.lead_id)
            .where(
                Lead.user_id == message.user_id,
                Appointment.created_at >= message.created_at - _BOOKING_WINDOW,
                Appointment.created_at <= message.created_at + timedelta(minutes=1),
            )
            .order_by(Appointment.created_at.desc())
            .limit(1)
        )
    ).first()
    if appt is None:
        return None
    appointment, lead_name = appt
    local = appointment.scheduled_at.astimezone(ZoneInfo(DEFAULT_BOOKING_TIMEZONE))
    shown_time = _format_display_time(local.strftime("%H:%M"))
    name = lead_name or (user.name if user else None) or "there"
    count = len(template.param_labels) if template.param_labels is not None else 3
    return ([name, local.strftime("%Y-%m-%d"), shown_time] + [shown_time] * count)[:count], None


async def main(apply: bool, org_id: UUID | None) -> None:
    print(f"database host: {engine.url.host}  mode: {'APPLY' if apply else 'dry run'}")
    fixed = skipped = 0
    async with AsyncSessionLocal() as db:
        stmt = select(Message).where(
            Message.channel == "whatsapp",
            Message.role == "assistant",
            Message.content.like("[template:%"),
        )
        if org_id is not None:
            stmt = stmt.where(Message.org_id == org_id)
        messages = (await db.execute(stmt.order_by(Message.created_at))).scalars().all()
        print(f"legacy template rows: {len(messages)}")
        for message in messages:
            found = LEGACY_TEMPLATE_MARKER.match(message.content or "")
            if not found:
                continue
            name = found.group(1)
            template = (
                await db.execute(
                    select(WhatsAppTemplate).where(
                        WhatsAppTemplate.org_id == message.org_id, WhatsAppTemplate.name == name
                    )
                )
            ).scalars().first()
            if template is None or not (template.body_preview or "").strip():
                skipped += 1
                continue
            source = await _task_params(db, message, name)
            if source is None and "confirm" in name:
                source = await _booking_params(db, message, template, await db.get(User, message.user_id))
            if source is None:
                skipped += 1
                continue
            body_params, header_params = source
            text = render_template_message(template, name, body_params, header_params)
            if fixed < 5:
                print(f"  {message.created_at:%Y-%m-%d %H:%M} {name}: {text[:110]!r}".encode("ascii", "replace").decode())
            if apply:
                message.content = text
            fixed += 1
        if apply:
            await db.commit()
    print(f"{'updated' if apply else 'would update'}: {fixed}   left as-is (no source): {skipped}")
    if not apply and fixed:
        print("dry run only — re-run with --apply to write")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the changes (default is a dry run)")
    parser.add_argument("--org", type=UUID, default=None, help="limit to one org id")
    args = parser.parse_args()
    asyncio.run(main(args.apply, args.org))
