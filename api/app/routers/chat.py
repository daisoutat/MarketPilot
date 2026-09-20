"""Assistant chat endpoints (Phase 7).

  POST   /api/v1/chat/send             ask a question (persists the exchange)
  GET    /api/v1/chat/messages         past messages of a session
  DELETE /api/v1/chat/sessions/{id}    wipe a session's history
  GET    /api/v1/chat/prefs            assistant customisation panel state
  PATCH  /api/v1/chat/prefs            update customisation (market/lang/…)
  GET    /api/v1/chat/predictions      forecast insights (gainers/decliners/top)

Answers come from `common.chat.assistant_reply`: deterministic NLU over the
LIVE database (bilingual EN/FR), always accompanied by structured `data` the
SPA can render beside the natural-language text. No external LLM is used.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select

from common.chat import assistant_reply, detect_language
from common.db import get_session
from common.forecast import predictive_insights
from common.models import AppUser, ChatMessage
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user

router = APIRouter()

PREF_DEFAULTS: dict = {
    "market": "all",
    "language": "en",
    "currency": "USD",
    "digest": "daily",
    "quick": ["summary", "orders", "margin", "forecast", "alerts", "inventory"],
}


class ChatSend(BaseModel):
    message: str
    session_id: str | None = None
    lang: str | None = None


class PrefUpdate(BaseModel):
    market: str | None = None
    language: str | None = None
    currency: str | None = None
    digest: str | None = None
    quick: list[str] | None = None


def _reply_out(r: dict, created_at: datetime | None) -> dict:
    return {
        "intent": r["intent"],
        "text": r["text"],
        "data": r.get("data"),
        "created_at": (created_at or datetime.now(UTC)).isoformat(),
    }


async def _get_user(session: AsyncSession, sub: str) -> AppUser:
    user = (await session.execute(select(AppUser).where(AppUser.sub == sub))).scalar_one_or_none()
    if user is None:
        user = AppUser(sub=sub, email="", display_name="", role="viewer", prefs={})
        session.add(user)
        await session.flush()
    return user


@router.get("/chat/prefs")
async def get_prefs(
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await _get_user(session, user["sub"])
    chat = {**PREF_DEFAULTS, **(row.prefs or {}).get("chat", {})}
    return {"prefs": chat}


@router.patch("/chat/prefs")
async def patch_prefs(
    body: PrefUpdate,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await _get_user(session, user["sub"])
    chat = {**PREF_DEFAULTS, **(row.prefs or {}).get("chat", {})}
    updates = body.model_dump(exclude_none=True)
    if "market" in updates and updates["market"] not in {"all", "US", "CA"}:
        raise HTTPException(422, f"unknown market: {updates['market']}")
    if "language" in updates and updates["language"] not in {"en", "fr"}:
        raise HTTPException(422, f"unknown language: {updates['language']}")
    if "digest" in updates and updates["digest"] not in {"daily", "weekly", "off"}:
        raise HTTPException(422, f"unknown digest: {updates['digest']}")
    chat.update(updates)
    prefs = row.prefs or {}
    prefs["chat"] = chat
    row.prefs = prefs
    await session.commit()
    return {"prefs": chat}


@router.post("/chat/send")
async def send(
    body: ChatSend,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    message = body.message.strip()
    if not message:
        raise HTTPException(422, "empty message")
    row = await _get_user(session, user["sub"])
    chat = {**PREF_DEFAULTS, **(row.prefs or {}).get("chat", {})}

    session_id = body.session_id or f"{user['sub'][:24]}-{int(datetime.now(UTC).timestamp())}"
    lang = body.lang or chat.get("language") or ""
    pref_market = None if chat.get("market") in (None, "all") else chat["market"]

    user_msg = ChatMessage(
        session_id=session_id, role="user", intent="", text=message,
    )
    session.add(user_msg)
    await session.flush()

    reply = await assistant_reply(
        session,
        message,
        lang=detect_language(message) or lang,
        market=pref_market,
    )
    out = _reply_out(reply, None)
    assistant_msg = ChatMessage(
        session_id=session_id, role="assistant", intent=reply["intent"],
        text=reply["text"], data=reply.get("data"),
    )
    session.add(assistant_msg)
    await session.commit()

    return {
        "session_id": session_id,
        "reply": {**out, "created_at": assistant_msg.created_at.isoformat()},
        "prefs": chat,
    }


@router.get("/chat/messages")
async def list_messages(
    session_id: str,
    limit: int = 50,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    msgs = (
        await session.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.asc())
            .limit(min(max(limit, 1), 200))
        )
    ).scalars().all()
    return {
        "session_id": session_id,
        "messages": [
            {"id": m.id, "role": m.role, "intent": m.intent, "text": m.text,
             "data": m.data, "created_at": m.created_at.isoformat() if m.created_at else None}
            for m in msgs
        ],
    }


@router.delete("/chat/sessions/{session_id}")
async def delete_session(
    session_id: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await session.execute(delete(ChatMessage).where(ChatMessage.session_id == session_id))
    await session.commit()
    return {"deleted": True, "session_id": session_id}


@router.get("/chat/predictions")
async def predictions(
    limit: int = 6,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    insights = await predictive_insights(session, limit=min(max(limit, 1), 20))
    return {
        "insights": insights,
        "generated_at": insights["generated_at"].isoformat() if insights["generated_at"] else None,
    }