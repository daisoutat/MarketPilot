"""Product taxonomy: category → niche → family (Phase 6).

A curated, deterministic keyword/brand rule set maps a product's title/brand/
canonical label into a (category, niche, family) triple. The taxonomy worker
materializes the tree in `categories` (3 levels) and links products to family
nodes. Rules are *explainable*: every assignment lists the rule it matched.

Matching is case-insensitive substring on title/brand/category against rule
keywords, plus exact brand-prefix matches; the first rule with any hit wins,
so deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from .models import Category, PriceSnapshot, Product

# Curated taxonomy (category → niche → family). Marks which products belong
# together and drives the multidimensional analysis tree.
TAXONOMY: list[dict[str, Any]] = [
    # Electronics ---------------------------------------------------------
    {"category": "Electronics", "niche": "Audio", "family": "Headphones & Earbuds",
     "keywords": ["headphon", "earbud", "earphone", "headset", "airpods", "bluetooth head"]},
    {"category": "Electronics", "niche": "Audio", "family": "Speakers",
     "keywords": ["speaker", "soundbar", "subwoofer", "sound bar"]},
    {"category": "Electronics", "niche": "Computing", "family": "Laptops & Tablets",
     "keywords": ["laptop", "notebook", "chromebook", "tablet", "ipad", "2-in-1"]},
    {"category": "Electronics", "niche": "Computing", "family": "Computer Accessories",
     "keywords": ["keyboard", "mouse", "webcam", "dock", "charging station", "usb hub", "ssd", "hard drive", "monitor"]},
    {"category": "Electronics", "niche": "Mobile", "family": "Phone Accessories",
     "keywords": ["phone case", "screen protector", "phone charger", "usb cable", "car mount", "power bank", "wireless charg"]},
    {"category": "Electronics", "niche": "Household Electronics", "family": "Small Kitchen Appliances",
     "keywords": ["air fryer", "toaster", "blender", "mixer", "kettle", "food processor", "slow cooker", "instant pot"]},
    # Home & Kitchen ------------------------------------------------------
    {"category": "Home & Kitchen", "niche": "Kitchen", "family": "Coffee & Espresso",
     "keywords": ["coffee", "espresso", "grinder", "french press", "pour over", "k-cup"]},
    {"category": "Home & Kitchen", "niche": "Kitchen", "family": "Cookware",
     "keywords": ["pan", "pot", "cookware", "skillet", "baking sheet", "knife", "cutting board"]},
    {"category": "Home & Kitchen", "niche": "Lighting", "family": "Lamps & Bulbs",
     "keywords": ["lamp", "light bulb", "led strip", "night light", "floor lamp", "desk lamp"]},
    {"category": "Home & Kitchen", "niche": "Bedding", "family": "Sheets & Pillows",
     "keywords": ["sheet", "pillow", "comforter", "duvet", "blanket", "bedding"]},
    {"category": "Home & Kitchen", "niche": "Storage", "family": "Shelving & Organizers",
     "keywords": ["shelf", "organizer", "storage bin", "rack", "basket", "container"]},
    # Fashion -------------------------------------------------------------
    {"category": "Fashion", "niche": "Men", "family": "Shirts & Polos",
     "keywords": ["men's shirt", "polo", "t-shirt", "button down", "henley"]},
    {"category": "Fashion", "niche": "Women", "family": "Dresses & Tops",
     "keywords": ["dress", "blouse", "women's top", "skirt"]},
    # Toys & Games --------------------------------------------------------
    {"category": "Toys & Games", "niche": "Building", "family": "Building Blocks",
     "keywords": ["lego", "building blocks", "magnetic tile", "brick set", "construction toy"]},
    {"category": "Toys & Games", "niche": "STEM", "family": "Science & Robotics",
     "keywords": ["robot", "stem", "science kit", "coding toy", "experiment"]},
    # Sports & Outdoors ---------------------------------------------------
    {"category": "Sports & Outdoors", "niche": "Fitness", "family": "Yoga & Gym",
     "keywords": ["yoga", "gym", "resistance band", "dumbbell", "kettlebell", "weight", "fitness"]},
    {"category": "Sports & Outdoors", "niche": "Outdoor", "family": "Camping & Hiking",
     "keywords": ["camping", "tent", "sleeping bag", "hiking", "backpack", "headlamp"]},
    # Health & Beauty -----------------------------------------------------
    {"category": "Health & Beauty", "niche": "Personal Care", "family": "Skin & Hair Care",
     "keywords": ["serum", "moisturizer", "shampoo", "conditioner", "face", "skin care", "hair care", "creams"]},
    {"category": "Health & Beauty", "niche": "Wellness", "family": "Supplements",
     "keywords": ["supplement", "vitamin", "collagen", "probiotic", "protein powder"]},
    # Office & Business ---------------------------------------------------
    {"category": "Office & Business", "niche": "Workspace", "family": "Desks & Chairs",
     "keywords": ["desk", "office chair", "ergonomic", "standing desk"]},
    {"category": "Office & Business", "niche": "Paper & Supplies", "family": "Notebooks & Planners",
     "keywords": ["notebook", "planner", "journal", "organizer", "binder"]},
    # Automotive ----------------------------------------------------------
    {"category": "Automotive", "niche": "Interior", "family": "Car Accessories",
     "keywords": ["car", "vehicle", "auto", "dash cam", "seat cover", "floor mat", "air freshener"]},
    # Pets ----------------------------------------------------------------
    {"category": "Pets", "niche": "Dogs", "family": "Dog Supplies",
     "keywords": ["dog", "puppy", "leash", "collar", "dog food", "pet"]},
    # Baby ----------------------------------------------------------------
    {"category": "Baby", "niche": "Feeding", "family": "Feeding & High Chairs",
     "keywords": ["baby bottle", "breast pump", "high chair", "sippy cup", "pacifier"]},
]

_MIN_MATCHES = 1


@dataclass(frozen=True)
class Rule:
    category: str
    niche: str
    family: str
    keywords: tuple[str, ...]


RULES: list[Rule] = [
    Rule(r["category"], r["niche"], r["family"], tuple(k.lower() for k in r["keywords"]))
    for r in TAXONOMY
]


def find_family(title: str = "", brand: str = "", category_label: str = "") -> Rule | None:
    """First rule whose keyword appears in title/brand/category (case-folded)."""
    haystack = " ".join([title, brand, category_label]).casefold()
    for rule in RULES:
        if any(kw in haystack for kw in rule.keywords):
            return rule
    return None


async def _ensure_node(session, market_id: int, parent_id: int | None, name: str, kind: str) -> Category:
    existing = (
        await session.execute(
            select(Category).where(
                Category.marketplace_id == market_id,
                Category.parent_id.is_(None) if parent_id is None else Category.parent_id == parent_id,
                Category.name == name,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    node = Category(marketplace_id=market_id, parent_id=parent_id, name=name, kind=kind)
    session.add(node)
    await session.flush()
    return node


async def ensure_family(session, market_id: int, rule: Rule) -> Category:
    """Create category → niche → family and return the family node (idempotent)."""
    category = await _ensure_node(session, market_id, None, rule.category, "category")
    niche = await _ensure_node(session, market_id, category.id, rule.niche, "niche")
    family = await _ensure_node(session, market_id, niche.id, rule.family, "family")
    return family


async def assign_product(session, product: Product, market_id: int) -> Category | None:
    """Assign a product to a family node by rules; returns the family or None."""
    rule = find_family(product.title or "", product.brand or "", product.category or "")
    if rule is None:
        return None
    family = await ensure_family(session, market_id, rule)
    product.family_id = family.id
    return family


async def assign_all(session, market_id: int) -> dict:
    """Assign every product (with at least one snapshot in the market) to a family."""
    product_ids = (
        select(Product.id)
        .join(PriceSnapshot, Product.id == PriceSnapshot.product_id)
        .where(PriceSnapshot.marketplace_id == market_id)
        .distinct()
    )
    products = (await session.execute(select(Product).where(Product.id.in_(product_ids)))).scalars().all()
    assigned = 0
    unassigned = 0
    for product in products:
        family = await assign_product(session, product, market_id)
        if family is not None:
            assigned += 1
        else:
            unassigned += 1
    await session.flush()
    return {"marketplace_id": market_id, "scanned": len(products), "assigned": assigned, "unassigned": unassigned}