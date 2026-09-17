"""Platform-wide social/contact links — prompt helper.

A leaf module (imports only the model), shared by both system-prompt
builders (``core/agent.py`` and ``channels/voice/realtime_bridge.py``), so
every org's agent always has the same set of social links on hand and can
share them when asked. These are the one platform-wide set an admin manages
from Settings → Social Links (superuser-only, ``PlatformSettings.social_links``
— see ``routers/billing.py``'s ``/platform-settings`` and ``/social-links``),
not a per-org setting — same pattern as ``core/whatsapp_assets.py``'s
``asset_catalog_prompt_block`` for the prompt-block shape.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models.platform_settings import PlatformSettings

# Ordered so the prompt block lists platforms consistently regardless of
# insertion order in the JSON — matches SOCIAL_FIELDS in
# apps/web/src/components/settings/social-links-panel.tsx.
KNOWN_SOCIAL_PLATFORMS: tuple[str, ...] = (
    "website",
    "instagram",
    "facebook",
    "twitter",
    "linkedin",
    "youtube",
    "whatsapp",
)


async def load_social_links(db: AsyncSession) -> dict[str, str]:
    record = await db.get(PlatformSettings, 1)
    if record is None or not record.social_links:
        return {}
    return {k: v for k, v in record.social_links.items() if v and str(v).strip()}


async def social_links_prompt_block(db: AsyncSession) -> str:
    """A short system-prompt block listing the platform's social links, or
    ``""`` when none are configured."""
    links = await load_social_links(db)
    if not links:
        return ""
    lines = ["Your organization's social/contact links:"]
    ordered = [p for p in KNOWN_SOCIAL_PLATFORMS if p in links]
    ordered += [p for p in links if p not in KNOWN_SOCIAL_PLATFORMS]
    for platform in ordered:
        label = platform.replace("_", " ").title()
        lines.append(f"- {label}: {links[platform]}")
    lines.append(
        "If the contact asks for a specific platform (e.g. \"your instagram\"), share only "
        "that one. If they ask generically for your \"social links\", \"social media\", "
        "\"socials\", or similar without naming a platform, share ALL of the links listed "
        "above together in one reply, not just one of them."
    )
    return "\n".join(lines)
