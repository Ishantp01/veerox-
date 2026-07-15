# Veerox Web — Admin Dashboard

Next.js 14 (App Router) admin dashboard for the Veerox AI backend.

## Getting started

```bash
cd apps/web
cp .env.example .env.local    # fill in NEXT_PUBLIC_API_URL
npm install
npm run dev                   # http://localhost:3001
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | Yes | Base URL of the FastAPI backend, e.g. `http://localhost:8001`. No trailing slash. |

The admin token is **not** an env var — it is entered via the `/login` page and persisted in `localStorage` under the key `veerox_admin_token`. It is injected as the `X-Admin-Token` header on every API request.

## Page map

| Route | File | Description |
|---|---|---|
| `/` | `src/app/page.tsx` | Dashboard home — four stat cards (users, calls, leads, p50 latency) |
| `/conversations` | `src/app/conversations/page.tsx` | Table of recent conversations |
| `/conversations/[id]` | `src/app/conversations/[id]/page.tsx` | Transcript viewer |
| `/leads` | `src/app/leads/page.tsx` | Leads table with intent filter |
| `/settings` | `src/app/settings/page.tsx` | Read-only env/config display |
| `/login` | `src/app/login/page.tsx` | Admin token entry |

## API client

`src/lib/api.ts` exports `apiFetch<T>(path, init?)`:

- Prepends `process.env.NEXT_PUBLIC_API_URL` to `path`.
- Reads `localStorage.getItem("veerox_admin_token")` and injects it as `X-Admin-Token`.
- Throws a descriptive `Error` on any non-2xx response.
- Returns the parsed JSON body typed as `T`.

Example:

```ts
import { apiFetch } from "@/lib/api";
import type { Stats } from "@/lib/types";

const stats = await apiFetch<Stats>("/admin/stats");
```

## Available scripts

| Script | Command |
|---|---|
| Dev server | `npm run dev` |
| Production build | `npm run build` |
| Start production | `npm run start` |
| Lint | `npm run lint` |
| Type-check | `npm run typecheck` |
