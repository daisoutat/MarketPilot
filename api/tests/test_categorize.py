# -*- coding: utf-8 -*-
"""Phase 6: taxonomy rules + tree materialization + product assignment."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from common.categorize import (
    RULES,
    assign_all,
    assign_product,
    ensure_family,
    find_family,
)
from common.models import Category, Marketplace, PriceSnapshot, Product
from common.orders import upsert_product


def test_rules_texture():
    # deterministic matching, case-insensitive, substring on title/brand/label
    assert find_family(title="Wireless Bluetooth Headphones Pro").family == "Headphones & Earbuds"
    assert find_family(title="Sonic Boom speaker 200W").family == "Speakers"
    assert find_family(brand="Acme", title="Coffee Maker Deluxe").family == "Coffee & Espresso"
    assert find_family(title="Frog Food Dispenser") is None  # no rule matched


def test_all_rules_have_distinct_families():
    names = [(r.category, r.niche, r.family) for r in RULES]
    assert len(set(names)) == len(names)


async def test_ensure_family_idempotent(seeded_session):
    rule = find_family(title="Wireless Headphones")
    assert rule is not None
    market = (await seeded_session.execute(select(Marketplace).where(Marketplace.code == "US"))).scalar_one()

    fam1 = await ensure_family(seeded_session, market.id, rule)
    await seeded_session.commit()
    fam2 = await ensure_family(seeded_session, market.id, rule)
    assert fam1.id == fam2.id

    nodes = (await seeded_session.execute(select(Category))).scalars().all()
    kinds = {n.kind for n in nodes}
    assert kinds == {"category", "niche", "family"}
    root = next(n for n in nodes if n.kind == "category")
    assert root.parent_id is None


async def test_assign_product_sets_family(seeded_session):
    market = (await seeded_session.execute(select(Marketplace).where(Marketplace.code == "US"))).scalar_one()
    pid = await upsert_product(seeded_session, "B0AUDIO", "Wireless Headphones Noise Cancelling")
    product = (await seeded_session.execute(select(Product).where(Product.id == pid))).scalar_one()
    family = await assign_product(seeded_session, product, market.id)
    await seeded_session.commit()
    assert family is not None and family.kind == "family"
    assert product.family_id == family.id


async def test_assign_all_scans_snapshotted_products(seeded_session):
    market = (await seeded_session.execute(select(Marketplace).where(Marketplace.code == "US"))).scalar_one()
    pid1 = await upsert_product(seeded_session, "B0COFFEE", "Espresso Machine Stainless")
    pid2 = await upsert_product(seeded_session, "B0NOMATCH", "Custom Widget Thing")
    seeded_session.add_all([
        PriceSnapshot(product_id=pid1, marketplace_id=market.id, ours=True, price=99.0, currency="USD"),
        PriceSnapshot(product_id=pid2, marketplace_id=market.id, ours=True, price=5.0, currency="USD"),
    ])
    await seeded_session.commit()

    outcome = await assign_all(seeded_session, market.id)
    await seeded_session.commit()

    assert outcome["scanned"] == 2
    assert outcome["assigned"] == 1  # only the espresso matches a rule
    assert outcome["unassigned"] == 1
    unchanged = (await seeded_session.execute(select(Product).where(Product.asin == "B0NOMATCH"))).scalar_one()
    assert unchanged.family_id is None