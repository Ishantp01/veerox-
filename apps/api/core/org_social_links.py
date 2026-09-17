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
        "ONLY share the links actually listed above — this is the complete, current set. If "
        "the contact asks for a platform that is NOT listed above (e.g. Twitter, when only "
        "Instagram and YouTube are listed), say you don't have that link to share right now "
        "instead of guessing, inventing, or reusing a URL from memory/training data. Never "
        "fabricate a URL for any platform, listed or not."
    )
    lines.append(
        "If the contact asks for a specific platform (e.g. \"your instagram\") and it IS "
        "listed above, share only that one. If they ask generically for your \"social "
        "links\", \"social media\", \"socials\", or similar without naming a platform, share "
        "ALL of the links listed above together in one reply, not just one of them."
    )
    lines.append(
        "Format the reply for WhatsApp, not Markdown: WhatsApp does NOT render "
        "[text](url) links or **double-asterisk** bold — those show up as broken literal "
        "text. Write each platform name in *single-asterisk* bold followed by a colon and "
        "the bare URL on its own line (e.g. \"*Instagram:* https://instagram.com/yourpage\"), "
        "with a plain URL exactly as given above — never invent, shorten, or guess a "
        "different URL than the one listed."
    )
    return "\n".join(lines)
