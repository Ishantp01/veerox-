from __future__ import annotations

import csv
import io
from uuid import UUID

import openpyxl
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from apps.api.core.tools import _normalize_phone
from apps.api.db.models import Contact
from apps.api.deps import DbDep, RequestAccountUserDep, RequestOrgDep, verify_admin_or_session
from apps.api.routers.admin import (
    _E164_PATTERN,
    _csv_streaming_response,
    _iter_csv_rows,
    _iter_xlsx_rows,
)
from apps.api.schemas.crm import (
    ContactCreate,
    ContactImportError,
    ContactImportResult,
    ContactOut,
    ContactUpdateIn,
    ContactWithLeadsOut,
)

router = APIRouter(prefix="/crm", tags=["crm"], dependencies=[Depends(verify_admin_or_session)])

_CONTACT_SAMPLE_ROWS = [
    {"name": "Asha Verma", "phone": "+919876543210", "email": "asha@example.com", "company": "Acme Inc."},
    {"name": "Rohit Singh", "phone": "+919812345678", "email": "", "company": ""},
]


@router.get("/contacts", response_model=list[ContactOut])
async def list_contacts(
    db: DbDep,
    org_id: RequestOrgDep,
    account_user_id: RequestAccountUserDep,
    q: str | None = Query(None, description="Filter by name/phone substring"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[Contact]:
    """Siloed by creator — every caller, admin included, only ever sees
    contacts they personally created (see db/models/contact.py)."""
    stmt = (
        select(Contact)
        .where(Contact.org_id == org_id, Contact.created_by_account_user_id == account_user_id)
        .order_by(Contact.created_at.desc())
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where((Contact.name.ilike(like)) | (Contact.phone.ilike(like)))
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("/contacts", response_model=ContactOut, status_code=201)
async def create_contact(
    payload: ContactCreate, db: DbDep, org_id: RequestOrgDep, account_user_id: RequestAccountUserDep
) -> Contact:
    contact = Contact(
        org_id=org_id,
        name=payload.name,
        phone=payload.phone,
        email=payload.email,
        company=payload.company,
        tags=payload.tags,
        owner_user_id=payload.owner_user_id,
        created_by_account_user_id=account_user_id,
    )
    db.add(contact)
    await db.commit()
    await db.refresh(contact)
    return contact


@router.get("/contacts/sample.csv")
async def sample_contacts_csv() -> StreamingResponse:
    """Blank-data template for POST /contacts/import. Registered ahead of
    GET /contacts/{contact_id} so its literal path isn't swallowed by that
    route's UUID path param."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["name", "phone", "email", "company"])
    for row in _CONTACT_SAMPLE_ROWS:
        writer.writerow([row["name"], row["phone"], row["email"], row["company"]])
    buf.seek(0)
    return _csv_streaming_response(buf.getvalue(), "contacts-sample.csv")


@router.get("/contacts/sample.xlsx")
async def sample_contacts_xlsx() -> StreamingResponse:
    """Same template as GET /contacts/sample.csv, as an .xlsx workbook."""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Contacts"
    sheet.append(["name", "phone", "email", "company"])
    for row in _CONTACT_SAMPLE_ROWS:
        sheet.append([row["name"], row["phone"], row["email"], row["company"]])
    for cell in sheet["B"][1:]:
        cell.number_format = "@"

    buf = io.BytesIO()
    workbook.save(buf)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="contacts-sample.xlsx"'},
    )


@router.post("/contacts/import", response_model=ContactImportResult)
async def import_contacts_file(
    db: DbDep,
    org_id: RequestOrgDep,
    account_user_id: RequestAccountUserDep,
    file: UploadFile = File(...),
) -> ContactImportResult:
    """Bulk-import contacts from an uploaded CSV or Excel (.xlsx) file. Only
    'phone' is required (name/email/company are optional columns). A row
    whose phone matches a contact the importer already owns updates that
    contact's other fields instead of erroring on the (org_id, phone) unique
    constraint — reimporting the same list is a safe way to refresh your own
    contacts. A phone already used by a contact someone ELSE in the org
    created is reported as an error row instead — the org-wide phone
    uniqueness constraint means it can't become a second, separately-owned
    contact, and it isn't the importer's to silently overwrite (see
    db/models/contact.py's creator-siloing docstring).
    Registered ahead of GET /contacts/{contact_id} so its literal path isn't
    swallowed by that route's UUID path param.
    """
    filename = (file.filename or "").lower()
    raw = await file.read()
    if filename.endswith(".csv"):
        rows = list(_iter_csv_rows(raw))
    elif filename.endswith(".xlsx"):
        rows = list(_iter_xlsx_rows(raw))
    else:
        raise HTTPException(status_code=400, detail="Only .csv or .xlsx files are supported")

    org_result = await db.execute(select(Contact.phone).where(Contact.org_id == org_id))
    own_result = await db.execute(
        select(Contact).where(
            Contact.org_id == org_id, Contact.created_by_account_user_id == account_user_id
        )
    )
    existing_by_phone = {c.phone: c for c in own_result.scalars().all()}
    other_owned_phones = set(org_result.scalars().all()) - set(existing_by_phone.keys())

    imported = 0
    updated = 0
    errors: list[ContactImportError] = []
    for row_num, row in rows:
        phone = row.get("phone", "")
        if not phone:
            errors.append(ContactImportError(row=row_num, reason="missing phone"))
            continue
        normalized = _normalize_phone(phone)
        if not _E164_PATTERN.match(normalized):
            errors.append(
                ContactImportError(
                    row=row_num,
                    reason=(
                        f"phone '{phone}' must include a country code in E.164 format, "
                        "e.g. +919876543210"
                    ),
                )
            )
            continue
        if normalized in other_owned_phones:
            errors.append(
                ContactImportError(
                    row=row_num,
                    reason=f"phone '{phone}' already belongs to another team member's contact",
                )
            )
            continue

        name = row.get("name") or None
        email = row.get("email") or None
        company = row.get("company") or None
        tags = [t.strip() for t in row.get("tags", "").split(",") if t.strip()] or None

        existing = existing_by_phone.get(normalized)
        if existing is not None:
            if name:
                existing.name = name
            if email:
                existing.email = email
            if company:
                existing.company = company
            if tags:
                existing.tags = tags
            updated += 1
        else:
            contact = Contact(
                org_id=org_id,
                name=name,
                phone=normalized,
                email=email,
                company=company,
                tags=tags,
                created_by_account_user_id=account_user_id,
            )
            db.add(contact)
            existing_by_phone[normalized] = contact
            imported += 1

    await db.commit()
    return ContactImportResult(imported=imported, updated=updated, skipped=len(errors), errors=errors)


@router.get("/contacts/{contact_id}", response_model=ContactWithLeadsOut)
async def get_contact(
    contact_id: UUID, db: DbDep, org_id: RequestOrgDep, account_user_id: RequestAccountUserDep
) -> Contact:
    stmt = (
        select(Contact)
        .where(
            Contact.id == contact_id,
            Contact.org_id == org_id,
            Contact.created_by_account_user_id == account_user_id,
        )
        .options(selectinload(Contact.leads))
    )
    result = await db.execute(stmt)
    contact = result.scalar_one_or_none()
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


@router.patch("/contacts/{contact_id}", response_model=ContactOut)
async def update_contact(
    contact_id: UUID,
    payload: ContactUpdateIn,
    db: DbDep,
    org_id: RequestOrgDep,
    account_user_id: RequestAccountUserDep,
) -> Contact:
    stmt = select(Contact).where(
        Contact.id == contact_id,
        Contact.org_id == org_id,
        Contact.created_by_account_user_id == account_user_id,
    )
    contact = (await db.execute(stmt)).scalar_one_or_none()
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    fields = payload.model_dump(exclude_unset=True)
    if "phone" in fields and not fields["phone"]:
        raise HTTPException(status_code=400, detail="Phone cannot be empty")
    for field, value in fields.items():
        setattr(contact, field, value)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="Another contact in this org already has that phone number"
        )
    await db.refresh(contact)
    return contact


@router.delete("/contacts/{contact_id}")
async def delete_contact(
    contact_id: UUID, db: DbDep, org_id: RequestOrgDep, account_user_id: RequestAccountUserDep
) -> dict[str, bool]:
    """Delete a contact. Leads/appointments that reference it (``ondelete="SET NULL"``
    on their ``contact_id`` FK) are kept — they just lose the contact link, not deleted."""
    stmt = select(Contact).where(
        Contact.id == contact_id,
        Contact.org_id == org_id,
        Contact.created_by_account_user_id == account_user_id,
    )
    contact = (await db.execute(stmt)).scalar_one_or_none()
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    await db.delete(contact)
    await db.commit()
    return {"ok": True}
