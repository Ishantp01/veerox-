"""Org social/contact links — lookup + prompt helper.

A leaf module (imports only the model), shared by both system-prompt
builders (``core/agent.py`` and ``channels/voice/realtime_bridge.py``), so
the agent always has the org's social links on hand and can share them when
asked, without the org having to paste them into its script text — same
pattern as ``core/whatsapp_assets.py``'s ``asset_catalog_prompt_block``.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models.org import Org

# Ordered so the prompt block (and the settings form) list platforms in a
# consistent, predictable order regardless of insertion order in the JSON.
KNOWN_SOCIAL_PLATFORMS: tuple[str, ...] = (
    "website",
    "instagram",
    "facebook",
    "twitter",
    "linkedin",
    "youtube",
    "tiktok",
    "google_maps",
)


async def load_org_social_links(db: AsyncSession, org_id: UUID) -> dict[str, str]:
    org = await db.get(Org, org_id)
    if org is None or not org.social_links:
        return {}
    return {k: v for k, v in org.social_links.items() if v and v.strip()}


async def social_links_prompt_block(db: AsyncSession, org_id: UUID) -> str:
    """A short system-prompt block listing the org's social links, or ``""``
    when none are configured."""
    links = await load_org_social_links(db, org_id)
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
