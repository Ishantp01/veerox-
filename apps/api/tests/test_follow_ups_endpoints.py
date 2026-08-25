"""Tests for apps.api.routers.follow_ups.

Covers a real bug found while manually testing the follow-up feature:
DELETE /follow-up-rules/{id} 500'd whenever one of the rule's tasks
belonged to a lead that ALSO already had its own independent "builtin"
follow-up task (rule_id IS NULL, template_name IS NULL) — the DB's plain
ON DELETE SET NULL on FollowUpTask.rule_id would try to null this task's
rule_id too, producing two such rows for the same lead and violating the
uq_follow_up_tasks_lead_builtin partial unique index (migration
e7b3a5c9f2d4). See delete_follow_up_rule's docstring for the fix.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.security import generate_login_token, hash_token
from apps.api.db.models import AccountUser, FollowUpRule, FollowUpTask, Lead, Org, OrgMembership, User

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ADMIN_HEADERS = {"X-Admin-Token": settings.admin_token}


async def _seed_org(db: AsyncSession) -> None:
    db.add(Org(id=ORG_ID, name="Test Org"))
    await db.commit()


async def _seed_org_and_login(client: AsyncClient, db: AsyncSession, org_id: uuid.UUID, email: str) -> dict[str, str]:
    db.add(Org(id=org_id, name=f"Org {email}"))
    await db.flush()
    login_token = generate_login_token()
    account = AccountUser(email=email, token_hash=hash_token(login_token))
    db.add(account)
    await db.flush()
    db.add(OrgMembership(org_id=org_id, account_user_id=account.id, role="admin"))
    await db.commit()

    login = await client.post("/auth/login", json={"token": login_token})
    return {"X-Session-Token": login.json()["token"]}


async def _seed_lead(db: AsyncSession, phone: str) -> Lead:
    user = User(org_id=ORG_ID, phone=phone)
    db.add(user)
    await db.flush()
    lead = Lead(org_id=ORG_ID, user_id=user.id, phone=phone, channel="voice", status="new")
    db.add(lead)
    await db.commit()
    return lead


async def test_delete_rule_nulls_task_rule_id_when_no_conflict(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    lead = await _seed_lead(db_session, "+910000000101")
    rule = FollowUpRule(
        org_id=ORG_ID, name="r", trigger_type="status_change",
        trigger_config={"status": "new", "delay_hours": 0}, channel="voice",
    )
    db_session.add(rule)
    await db_session.flush()
    task = FollowUpTask(
        org_id=ORG_ID, lead_id=lead.id, rule_id=rule.id,
        run_at=datetime.now(UTC), status="sent",
    )
    db_session.add(task)
    await db_session.commit()

    response = await client.delete(f"/follow-up-rules/{rule.id}", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    await db_session.refresh(task)
    assert task.rule_id is None


async def test_delete_rule_drops_task_that_would_collide_with_existing_builtin_task(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The lead already has its own rule_id=NULL/template_name=NULL task
    (its own Lead.follow_up_at trigger) — nulling the rule-task's rule_id
    too would create a duplicate under uq_follow_up_tasks_lead_builtin, so
    the rule-task should be deleted instead, leaving the pre-existing
    builtin task untouched."""
    await _seed_org(db_session)
    lead = await _seed_lead(db_session, "+910000000102")

    builtin_task = FollowUpTask(
        org_id=ORG_ID, lead_id=lead.id, rule_id=None,
        run_at=datetime.now(UTC) - timedelta(hours=1), status="sent",
    )
    db_session.add(builtin_task)
    await db_session.flush()

    rule = FollowUpRule(
        org_id=ORG_ID, name="r", trigger_type="status_change",
        trigger_config={"status": "new", "delay_hours": 0}, channel="voice",
    )
    db_session.add(rule)
    await db_session.flush()
    rule_task = FollowUpTask(
        org_id=ORG_ID, lead_id=lead.id, rule_id=rule.id,
        run_at=datetime.now(UTC), status="sent",
    )
    db_session.add(rule_task)
    await db_session.commit()
    rule_task_id = rule_task.id
    builtin_task_id = builtin_task.id

    response = await client.delete(f"/follow-up-rules/{rule.id}", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"ok": True}

    remaining = (
        await db_session.execute(select(FollowUpTask).where(FollowUpTask.lead_id == lead.id))
    ).scalars().all()
    remaining_ids = {t.id for t in remaining}
    assert rule_task_id not in remaining_ids
    assert builtin_task_id in remaining_ids
    assert len(remaining) == 1


async def test_delete_rule_404s_for_unknown_id(client: AsyncClient) -> None:
    response = await client.delete(f"/follow-up-rules/{uuid.uuid4()}", headers=ADMIN_HEADERS)
    assert response.status_code == 404


async def test_rules_are_scoped_to_the_callers_own_org(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Regression guard: every endpoint here used to resolve org via
    core.tools._default_org_id() unconditionally, ignoring the caller's own
    session — a rule created from ANY org's dashboard was silently written
    to (and only ever matched leads in) the one hardcoded default org, and
    every org's rule list/task list showed that same org's data regardless
    of who was actually logged in. Two distinct dashboard sessions must
    each only see, and only be able to act on, their own org's rules.
    """
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()
    headers_a = await _seed_org_and_login(client, db_session, org_a, "a@example.com")
    headers_b = await _seed_org_and_login(client, db_session, org_b, "b@example.com")

    create_response = await client.post(
        "/follow-up-rules",
        json={
            "name": "Org A's rule",
            "trigger_config": {"status": "new", "delay_hours": 0},
            "channel": "voice",
        },
        headers=headers_a,
    )
    assert create_response.status_code == 201
    rule = create_response.json()
    assert rule["org_id"] == str(org_a)
    rule_id = rule["id"]

    # Org B's own rule list must not include org A's rule.
    b_list = await client.get("/follow-up-rules", headers=headers_b)
    assert b_list.status_code == 200
    assert all(r["id"] != rule_id for r in b_list.json())

    # Org A's own list does include it.
    a_list = await client.get("/follow-up-rules", headers=headers_a)
    assert any(r["id"] == rule_id for r in a_list.json())

    # Org B can't update, delete, or otherwise reach org A's rule by id.
    patch_response = await client.patch(
        f"/follow-up-rules/{rule_id}", json={"active": False}, headers=headers_b
    )
    assert patch_response.status_code == 404

    delete_response = await client.delete(f"/follow-up-rules/{rule_id}", headers=headers_b)
    assert delete_response.status_code == 404

    # Org A can still act on its own rule.
    own_delete = await client.delete(f"/follow-up-rules/{rule_id}", headers=headers_a)
    assert own_delete.status_code == 200
