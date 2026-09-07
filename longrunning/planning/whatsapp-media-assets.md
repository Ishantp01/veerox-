# WhatsApp media assets — agent can send documents / images / videos

## Context

Today the WhatsApp agent can only send **text** (`send_whatsapp_message` tool →
`wa_client.send_text`). When a client asks for "the plan", "pricing", "your
brochure", the bot can only type it out — it can't attach the actual PDF or
image the business already has.

Meta's WhatsApp Cloud API supports sending `image` / `document` / `video`
messages using the **same credentials** we already use for text
(`meta_access_token` + `meta_phone_number_id`). The only missing pieces are:

1. somewhere for an org to store its files, and
2. a way for the agent to pick the right file and send it.

This feature adds an **org-scoped media library** (upload + manage in the
dashboard) and a new agent tool `send_whatsapp_file` that sends a saved file to
the contact over WhatsApp.

### Scope decisions (v1)

| Decision | Choice | Why |
|---|---|---|
| Storage | Bytes in Postgres (`whatsapp_assets.data`), served to Meta via an unguessable public URL | No new infra / secrets; fully self-serve for the client. Cap uploads at 16 MB. |
| Media types | `document`, `image`, `video` (one code path, `type` differs) | Meta payload is identical bar the type key. |
| Outside the 24 h window | Tool returns `status:"error", reason:"outside_24h_window"`; agent asks the contact to send a message first | Free-form media outside the window needs an approved **media-header template** — large extra scope, deferred. In a live chat the window is always open, so this is rare. |
| Voice calls | Tool is auto-exposed on voice too (shared `TOOL_DEFINITIONS`); catalog injected into the voice prompt as well | A caller asking "WhatsApp me the brochure" already works for text via `send_whatsapp_message`. |
| Plan limits | Not metered in v1 (mirrors `send_whatsapp_message`) | Keep first cut small. |

## Data model

New table `whatsapp_assets` (`apps/api/db/models/whatsapp_asset.py`, registered
in `apps/api/db/models/__init__.py`). Org-scoped exactly like `Script`:

| column | type | notes |
|---|---|---|
| `id` | UUID PK | |
| `org_id` | UUID FK → `orgs.id` `ondelete=CASCADE`, indexed | |
| `name` | `String(255)` | human label the agent matches on, e.g. "Price List 2026" |
| `description` | `Text` nullable | "what this is / when to send it" — fed to the agent |
| `media_type` | `String(16)` | `document` \| `image` \| `video` |
| `filename` | `String(255)` | original filename (Meta shows it for documents) |
| `mime_type` | `String(128)` | |
| `size_bytes` | `Integer` | |
| `data` | `LargeBinary` | the file bytes |
| `access_key` | `String(64)` | `secrets.token_urlsafe(32)`; required as `?k=` on the public URL |
| `created_at` / `updated_at` | tz-aware, `server_default=now()` | |

Migration: new file off current head `f7a8b9c0d1e2`, `op.create_table` +
`op.create_index('ix_whatsapp_assets_org_id', ...)` — pattern from
`migrations/versions/e7f8a9b0c1d2_add_scripts_table.py`. No backfill.

## Backend

### 1. Meta client — `apps/api/channels/whatsapp/client.py`

Add `send_media()` next to `send_text` (same try/except + `_meta_error_detail`
logging + return `r.json()`):

```python
async def send_media(
    to_e164: str,
    media_type: str,          # "document" | "image" | "video"
    link: str,
    caption: str | None = None,
    filename: str | None = None,
    phone_number_id: str | None = None,
) -> dict[str, Any]:
    obj: dict[str, Any] = {"link": link}
    if caption:
        obj["caption"] = caption
    if media_type == "document" and filename:
        obj["filename"] = filename
    payload = {
        "messaging_product": "whatsapp",
        "to": to_e164,
        "type": media_type,
        media_type: obj,
    }
    ...
```

### 2. Shared helpers — new leaf module `apps/api/core/whatsapp_assets.py`

Only imports the model + `settings` (no cycles). Used by the tool **and** both
prompt builders.

- `async def load_org_assets(db, org_id) -> list[WhatsAppAsset]`
- `def asset_public_url(asset) -> str` →
  `f"{settings.public_base_url.rstrip('/')}/media/wa-asset/{asset.id}?k={asset.access_key}"`
- `async def resolve_asset(db, org_id, name) -> WhatsAppAsset | list[str] | None`
  — exact case-insensitive match → single `ILIKE %name%` match → `None` (no
  match) or `list[str]` of names (ambiguous).
- `async def asset_catalog_prompt_block(db, org_id) -> str` — `""` when the org
  has no assets, else a short block:
  ```
  Files you can send to the contact over WhatsApp with send_whatsapp_file
  (pass the name exactly):
  - "Price List 2026" (document) — current pricing for all plans
  - "Company Brochure" (image) — one-pager overview
  Only send a file the contact actually asked for or that directly answers
  their question. Never name a file that isn't in this list.
  ```

### 3. Agent tool — `apps/api/core/tools.py`

- Add schema to `TOOL_DEFINITIONS` (after `send_whatsapp_message`), name
  `send_whatsapp_file`, args: `name` (string, required), `caption` (string,
  optional), `phone` (string, optional — same "omit = caller's own number"
  behaviour as `send_whatsapp_message`).
- Handler `async def send_whatsapp_file(db, name, caption=None, phone=None, *, user_id=None, org_id=None, **_)`:
  1. resolve phone the same way `send_whatsapp_message` does (lines 1032-1044)
  2. `resolve_asset(db, org_id, name)` → error `no_matching_file` (with
     `available` list) / `ambiguous_file_name` (with `matches`)
  3. `wa_client.send_media(phone, asset.media_type, asset_public_url(asset), caption, asset.filename, phone_number_id)`
  4. on `httpx.HTTPStatusError` with `_meta_error_code(exc) == _REENGAGEMENT_ERROR_CODE`
     → `{"status":"error","reason":"outside_24h_window"}`
  5. other errors → `{"status":"error","reason":"whatsapp_send_failed"}`
  6. ok → `{"status":"ok","phone":...,"file":asset.name}`
- Register in `DISPATCH_TABLE`.

### 4. Prompt injection

- `apps/api/core/agent.py::_system_prompt_for` — append
  `await asset_catalog_prompt_block(db, org_id)` when non-empty (both channels
  hit this for text; keep it channel-agnostic).
- `apps/api/channels/voice/realtime_bridge.py::_system_instructions` — same
  append before the return.

### 5. Public serve route — new `apps/api/routers/media.py`

`APIRouter(tags=["media"])` — **no auth dependency** (Meta's servers fetch it):

```python
@router.get("/media/wa-asset/{asset_id}")
async def serve_wa_asset(asset_id: UUID, k: str, db: DbDep) -> Response:
    asset = await db.get(WhatsAppAsset, asset_id)
    if asset is None or not secrets.compare_digest(k, asset.access_key):
        raise HTTPException(404)
    headers = {"Cache-Control": "private, max-age=300"}
    if asset.media_type == "document":
        headers["Content-Disposition"] = f'inline; filename="{asset.filename}"'
    return Response(content=asset.data, media_type=asset.mime_type, headers=headers)
```

Register in `apps/api/main.py` (`app.include_router(media.router)`).

### 6. Dashboard CRUD — `apps/api/routers/admin.py` + `apps/api/schemas/whatsapp_asset.py`

Mirror the `/admin/scripts` block (lines 2101-2204), prefix `/admin/whatsapp-assets`:

- `GET /admin/whatsapp-assets` → `list[WhatsAppAssetOut]` (no `data`/`access_key`
  in the schema — metadata only)
- `POST /admin/whatsapp-assets` — **multipart** (`file: UploadFile = File(...)`,
  `name: str = Form(...)`, `description: str | None = Form(None)`), pattern from
  `routers/crm.py::import_contacts_file` (lines 125-151). Reject >16 MB and
  unknown mime. `media_type` inferred from `file.content_type`
  (`image/*`→image, `video/*`→video, else document). `access_key =
  secrets.token_urlsafe(32)`.
- `PATCH /admin/whatsapp-assets/{id}` — rename / edit description only
  (`model_dump(exclude_unset=True)`, `id`+`org` guard like
  `update_script_library_item`)
- `DELETE /admin/whatsapp-assets/{id}` → `{"ok": True}`

All guarded by the router-level `verify_admin_or_session`; every query scoped
`.where(WhatsAppAsset.org_id == org)` with `org: RequestOrgDep`.

## Frontend (`apps/web`)

- `src/lib/types.ts` — `WhatsAppAsset` interface (id, org_id, name, description,
  media_type, filename, mime_type, size_bytes, created_at, updated_at).
- `src/lib/query.ts` — `queryKeys.whatsappAssets: () => ["whatsapp-assets"]`.
- `src/lib/hooks/useWhatsappAssets.ts` — `useWhatsappAssets` (GET),
  `useUploadWhatsappAsset` (raw `fetch` + `FormData`, pattern from
  `useCampaigns.ts` lines 69-110), `useUpdateWhatsappAsset` (PATCH via
  `apiFetch`), `useDeleteWhatsappAsset`. Export from `src/lib/hooks/index.ts`.
- `src/app/(dashboard)/whatsapp/media/page.tsx` — new route. `PageHeader` +
  `Card` + `QueryBoundary` + `Table` (Name / Type / Size / Description /
  Actions), an upload dialog (`<input type="file">` + name + description), row
  delete with `useConfirm`, row edit dialog. Structure copied from
  `components/settings/script-library.tsx` and
  `app/(dashboard)/whatsapp/templates/page.tsx`.
- `src/components/nav.tsx` — add `{ href: "/whatsapp/media", label: "WhatsApp Media", Icon: Paperclip, iconClassName: "text-green-500" }`
  to the **Communication** group (after WhatsApp Templates). Client-file
  library, not org back-office → **not** added to `MEMBER_RESTRICTED_HREFS`.

## Tests

- `apps/api/tests/test_whatsapp_client.py` (new) — `send_media` builds the right
  payload / raises on non-2xx (monkeypatch `wa_client._http`).
- `apps/api/tests/test_tools.py` — `send_whatsapp_file`: happy path
  (monkeypatch `tools.wa_client.send_media` capturing), `no_matching_file`,
  `outside_24h_window` (raise `HTTPStatusError` with code 131047).
- `apps/api/tests/test_media_routes.py` (new) — serve route: 200 + bytes with
  correct `k`, 404 on wrong/missing `k`, 404 on unknown id.
- `apps/api/tests/test_whatsapp_asset_endpoints.py` (new) — admin CRUD, pattern
  from `test_script_endpoints.py` (multipart upload via the `client` fixture,
  `ADMIN_HEADERS`).

## Verification (end to end)

1. `alembic upgrade head` — new table exists.
2. `pytest apps/api/tests/test_tools.py test_whatsapp_client.py test_media_routes.py test_whatsapp_asset_endpoints.py`
3. Dashboard → **WhatsApp Media** → upload a PDF named "Price List".
4. Hit `GET {public_base_url}/media/wa-asset/{id}?k={key}` → PDF bytes.
5. From a WhatsApp chat with the bot (24 h window open): "send me your price
   list" → bot calls `send_whatsapp_file` → PDF arrives in the chat.
6. Check `whatsapp_send_media_ok` in logs / `GET /diag/latency`.

## Deployment note

Meta fetches the `link` server-side, so `PUBLIC_BASE_URL` **must** be the real
public Render URL (already required for the Meta webhook — see
`veerox-deployment-setup` memory). No new env vars.
