# -*- coding: utf-8 -*-
"""Phase 7: assistant NLU, bilingual answers, chat API, prefs + predictive insights."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from common.chat import assistant_reply, detect_language, parse_intent
from common.forecast import compute_forecast, predictive_insights
from common.models import (
    Alert,
    Category,
    Forecast,
    FxRate,
    Inventory,
    Marketplace,
    Order,
    OrderItem,
    PriceSnapshot,
    Product,
    Seller,
    Settlement,
    SettlementLine,
)
from common.orders import upsert_product

US_ASIN = "B0CHAT0100"
CA_ASIN = "B0CHAT0200"


async def _seed_eco(session, market, seller, pid, asin, units, days_ago=2, revenue=100.0, net=40.0, currency="USD"):
    order = Order(
        seller_id=seller.id, marketplace_id=market.id,
        amazon_order_id="CHAT-%s-%d-%d" % (asin, days_ago, int(units)),
        purchase_date=datetime.now(UTC) - timedelta(days=days_ago), status="Shipped",
    )
    session.add(order)
    await session.flush()
    session.add(OrderItem(
        order_id=order.id, product_id=pid, asin=asin, seller_sku="SKU",
        quantity=units, unit_price=revenue, item_currency=currency,
    ))
    sett = Settlement(
        seller_id=seller.id, marketplace_id=market.id, posted_date=order.purchase_date,
        gross=revenue * units, fees=(revenue - net) * units, net=net * units, currency=currency,
    )
    session.add(sett)
    await session.flush()
    session.add(SettlementLine(
        settlement_id=sett.id, product_id=pid, asin=asin, sku="SKU",
        order_id=order.amazon_order_id, units=units, revenue=revenue * units,
        fees=(revenue - net) * units, net=net * units, currency=currency,
    ))


async def _seed_chat_world(session):
    """US + CA markets, 1 trending product + 1 declining, margins, stock, alerts."""
    us = (await session.execute(select(Marketplace).where(Marketplace.code == "US"))).scalar_one()
    ca = (await session.execute(select(Marketplace).where(Marketplace.code == "CA"))).scalar_one()
    seller = Seller(name="Chat Seller", credentials_ref="env")
    session.add(seller)
    await session.commit()

    session.add(FxRate(base="CAD", quote="USD", rate=0.75, refreshed_at=datetime.now(UTC)))
    await session.commit()

    p_up = await upsert_product(session, US_ASIN, "Bluetooth Headphones Pro")
    for i in range(40):  # 40 days of growing demand
        await _seed_eco(session, us, seller, p_up, US_ASIN,
                        units=2 + i // 4, days_ago=i, revenue=120.0, net=70.0)
    await session.commit()
    p_down = await upsert_product(session, CA_ASIN, "Bluetooth Earbuds Lite")
    for i in range(60):  # declining: heavy demand weeks ago, thin now (CA)
        await _seed_eco(session, ca, seller, p_down, CA_ASIN,
                        units=max(1, 25 - i), days_ago=61 - i, revenue=60.0, net=20.0,
                        currency="CAD")
    await session.commit()

    # price snapshots for US and CA (compare intent)
    session.add(PriceSnapshot(product_id=p_up, marketplace_id=us.id, ours=True,
                              price=129.99, sales_rank=120, currency="USD"))
    session.add(PriceSnapshot(product_id=p_up, marketplace_id=ca.id, ours=True,
                              price=159.99, sales_rank=90, currency="CAD"))
    # inventory
    session.add(Inventory(product_id=p_up, quantity=2, snapped_at=datetime.now(UTC)))
    session.add(Inventory(product_id=p_down, quantity=40, snapped_at=datetime.now(UTC)))
    # alerts: one critical open, one warning open, one resolved
    session.add(Alert(kind="stock_out", severity="critical", product_id=p_up,
                      marketplace_id=us.id, message="P1 nearly out of stock"))
    session.add(Alert(kind="price_drop", severity="warning", product_id=p_down,
                      marketplace_id=us.id, message="P2 price dropped 8%"))
    session.add(Alert(kind="margin_erosion", severity="warning", product_id=p_up,
                      marketplace_id=us.id, message="old one", resolved_at=datetime.now(UTC)))
    await session.commit()

    # stored forecasts for both products
    f_up = await compute_forecast(session, p_up, horizon=30)
    f_down = await compute_forecast(session, p_down, horizon=30)
    assert f_up is not None and f_down is not None
    session.add_all([
        Forecast(product_id=p_up, model=f_up["model"], unit="units/day", window_days=f_up["window_days"],
                 horizon=30, params=f_up["params"], history=f_up["history"], points=f_up["points"]),
        Forecast(product_id=p_down, model=f_down["model"], unit="units/day", window_days=f_down["window_days"],
                 horizon=30, params=f_down["params"], history=f_down["history"], points=f_down["points"]),
    ])
    await session.commit()
    return us, ca, seller, p_up, p_down


# ---------------------------------------------------------------------------
# NLU
# ---------------------------------------------------------------------------

def test_detect_language_en_fr():
    assert detect_language("show me sales overview") == "en"
    assert detect_language("bonjour, donne-moi les ventes du mois") == "fr"


def test_parse_intent_english_phrases():
    assert parse_intent("show me sales overview")[0] == "orders"
    assert parse_intent("what is our margin last 90 days")[0] == "margin"
    assert parse_intent("forecast for next month")[0] == "predict"
    assert parse_intent("any alerts?")[0] == "alerts"
    assert parse_intent("inventory levels")[0] == "inventory"
    assert parse_intent("compare US vs CA units")[0] == "compare"
    assert parse_intent("category performance")[0] == "analyze"
    assert parse_intent("what can you do")[0] == "help"
    assert parse_intent("hi there")[0] == "greeting"
    assert parse_intent("the quick brown fox")[0] == "unknown"


def test_parse_intent_french_phrases():
    assert parse_intent("bonjour, montre moi les ventes")[0] == "orders"
    assert parse_intent("quelle est la marge sur 90 jours")[0] == "margin"
    assert parse_intent("prévisions de la demande")[0] == "predict"
    assert parse_intent("alertes critiques")[0] == "alerts"
    assert parse_intent("inventaire du stock")[0] == "inventory"
    assert parse_intent("comparer les prix en devise")[0] == "compare"
    assert parse_intent("résumé de la situation")[0] == "summary"


def test_parse_intent_extracts_asin_market_days():
    intent, params = parse_intent("status of B0CHAT80W2 in CA for 60 days")
    assert intent == "asin"
    assert params["asin"] == "B0CHAT80W2"
    assert params["market"] == "CA"
    assert params["days"] == 60


def test_spa_quick_prompts_map_to_intents():
    """The exact canned prompts sent by the Assistant page must hit the right
    intent (regression guard: words like 'sales'/'ventes' match 'orders')."""
    cases = {
        "summary": [
            "Give me the big picture status over the last 90 days",
            "Donne-moi l'état des lieux sur 90 jours",
        ],
        "orders": [
            "Show me my recent orders",
            "Montre-moi mes dernières commandes",
        ],
        "margin": [
            "What is my margin over the last 90 days",
            "Quelle est ma marge sur les 90 derniers jours",
        ],
        "predict": [
            "What does the forecast say for the coming 30 days",
            "Quelle est la prévision de la demande",
        ],
        "alerts": [
            "Show me open alerts",
            "Montre-moi les alertes ouvertes",
        ],
        "inventory": [
            "What is my inventory level",
            "Quel est mon niveau de stock",
        ],
        "compare": [
            "Compare prices between US and CA",
            "Compare les prix entre le Canada et les États-Unis",
        ],
    }
    for expected, prompts in cases.items():
        for prompt in prompts:
            assert parse_intent(prompt)[0] == expected, (expected, prompt)


# ---------------------------------------------------------------------------
# Predictive insights
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_predictive_insights_ranks_products(seeded_session):
    await _seed_chat_world(seeded_session)
    insights = await predictive_insights(seeded_session, limit=5)
    assert insights["count"] == 2
    assert insights["gainers"] and insights["gainers"][0]["asin"] == US_ASIN
    assert insights["decliners"] and insights["decliners"][0]["asin"] == CA_ASIN
    assert insights["top_next_30d"][0]["next_30d"] > 0


# ---------------------------------------------------------------------------
# Assistant answers (data-grounded)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_assistant_summary_and_orders(seeded_session):
    await _seed_chat_world(seeded_session)
    ans = await assistant_reply(seeded_session, "sales overview")
    assert ans["intent"] == "orders"
    assert ans["data"]["total"]["orders"] > 0
    assert "unit" in ans["text"]
    assert ans["data"]["rows"]

    ans2 = await assistant_reply(seeded_session, "bonjour, résumé de la situation")
    assert ans2["intent"] == "summary"
    assert "commandes" in ans2["text"] or "unités" in ans2["text"]
    assert ans2["data"]["by_market"]


@pytest.mark.asyncio
async def test_assistant_margin(seeded_session):
    await _seed_chat_world(seeded_session)
    ans = await assistant_reply(seeded_session, "margin last 90 days")
    assert ans["intent"] == "margin"
    assert ans["data"]["rows"]
    assert ans["data"]["rows"][0]["margin_pct"] is not None


@pytest.mark.asyncio
async def test_assistant_inventory_and_alerts(seeded_session):
    await _seed_chat_world(seeded_session)
    inv = await assistant_reply(seeded_session, "inventory status")
    assert inv["intent"] == "inventory"
    assert inv["data"]["rows"]
    assert inv["data"]["rows"][0]["quantity"] < 10  # lowest stock first

    al = await assistant_reply(seeded_session, "any alerts?")
    assert al["intent"] == "alerts"
    assert al["data"]["count"] == 2
    assert al["data"]["critical"] == 1


@pytest.mark.asyncio
async def test_assistant_forecast_answer(seeded_session):
    await _seed_chat_world(seeded_session)
    ans = await assistant_reply(seeded_session, "what does the forecast say")
    assert ans["intent"] == "predict"
    assert ans["data"]["insights"]["count"] == 2
    assert ans["data"]["chart"]["type"] == "bar"


@pytest.mark.asyncio
async def test_assistant_compare_markets(seeded_session):
    await _seed_chat_world(seeded_session)
    ans = await assistant_reply(seeded_session, "compare US vs CA prices")
    assert ans["intent"] == "compare"
    rows = ans["data"]["rows"]
    assert rows and rows[0]["asin"] == US_ASIN
    assert rows[0]["gap_usd"] is not None


@pytest.mark.asyncio
async def test_assistant_asin_picture(seeded_session):
    await _seed_chat_world(seeded_session)
    ans = await assistant_reply(seeded_session, US_ASIN)
    assert ans["intent"] == "asin"
    assert ans["data"]["product"]["asin"] == US_ASIN
    assert ans["data"]["snapshot"]["price"] == 129.99
    assert ans["data"]["chart"]["type"] == "forecast"


@pytest.mark.asyncio
async def test_assistant_help_greeting_unknown(seeded_session):
    g = await assistant_reply(seeded_session, "bonjour")
    assert g["intent"] == "greeting"
    assert "assistant" in g["text"].casefold()
    h = await assistant_reply(seeded_session, "what can you do")
    assert h["intent"] == "help"
    u = await assistant_reply(seeded_session, "zzzz qqqq")
    assert u["intent"] == "unknown"


# ---------------------------------------------------------------------------
# Chat API
# ---------------------------------------------------------------------------

async def test_chat_send_persists_and_returns_reply(seeded_session, app_client):
    await _seed_chat_world(seeded_session)
    r = app_client.post("/api/v1/chat/send", json={"message": "sales overview"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"]
    assert body["reply"]["intent"] == "orders"
    assert body["reply"]["text"]
    assert body["prefs"]["language"] == "en"

    msgs = app_client.get("/api/v1/chat/messages", params={"session_id": body["session_id"]})
    assert msgs.status_code == 200
    assert len(msgs.json()["messages"]) == 2  # user + assistant
    assert msgs.json()["messages"][0]["role"] == "user"

    d = app_client.delete("/api/v1/chat/sessions/%s" % body["session_id"])
    assert d.status_code == 200
    assert app_client.get("/api/v1/chat/messages",
                          params={"session_id": body["session_id"]}).json()["messages"] == []


async def test_chat_prefs_roundtrip(seeded_session, app_client):
    r = app_client.get("/api/v1/chat/prefs")
    assert r.status_code == 200
    assert r.json()["prefs"]["language"] == "en"

    p = app_client.patch("/api/v1/chat/prefs", json={"language": "fr", "market": "CA", "digest": "weekly"})
    assert p.status_code == 200, p.text
    prefs = p.json()["prefs"]
    assert prefs["language"] == "fr"
    assert prefs["market"] == "CA"
    assert prefs["digest"] == "weekly"

    bad = app_client.patch("/api/v1/chat/prefs", json={"market": "MX"})
    assert bad.status_code == 422


async def test_chat_predictions_endpoint(seeded_session, app_client):
    await _seed_chat_world(seeded_session)
    r = app_client.get("/api/v1/chat/predictions", params={"limit": 4})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["insights"]["count"] == 2
    assert body["insights"]["gainers"][0]["asin"] == US_ASIN