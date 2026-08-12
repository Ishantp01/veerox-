"""In-app help-desk chatbot for org clients and their team members.

Scoped to Veerox product questions only via HELP_DESK_SYSTEM_PROMPT (or the
platform admin's override, see routers/billing.py's platform-settings
endpoints). Unlike core/agent.py's AgentCore, this is a single non-streaming
completion with no tool-calling loop and no server-side transcript
persistence - the frontend widget keeps the conversation in local state and
resends recent history each turn.
"""

from __future__ import annotations

from fastapi import APIRouter

from apps.api.core.llm import chat_completion
from apps.api.core.prompts import HELP_DESK_SYSTEM_PROMPT
from apps.api.deps import CurrentOrgDep, DbDep
from apps.api.db.models.platform_settings import PlatformSettings
from apps.api.schemas.helpdesk import HelpDeskChatIn, HelpDeskChatOut

router = APIRouter(prefix="/helpdesk", tags=["helpdesk"])

# Only recent turns are sent to keep the request small and on-topic; the
# widget itself may hold a longer local history.
_MAX_HISTORY_MESSAGES = 12


@router.post("/chat", response_model=HelpDeskChatOut)
async def helpdesk_chat(payload: HelpDeskChatIn, org: CurrentOrgDep, db: DbDep) -> HelpDeskChatOut:
    _ = org  # CurrentOrgDep only used to require *some* valid session
    settings_record = await db.get(PlatformSettings, 1)
    system_prompt = (
        settings_record.help_desk_script
        if settings_record is not None and settings_record.help_desk_script
        else HELP_DESK_SYSTEM_PROMPT
    )

    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt.strip()}]
    for turn in payload.history[-_MAX_HISTORY_MESSAGES:]:
        messages.append({"role": turn.role, "content": turn.content})
    messages.append({"role": "user", "content": payload.message})

    result = await chat_completion(messages)
    reply = result.content or "Sorry, I couldn't come up with a reply to that - could you rephrase?"
    return HelpDeskChatOut(reply=reply)
