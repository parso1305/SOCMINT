"""Synthetic demo fixtures (every row has synthetic=True; fictional brands, numbers, domains, symbols)."""

from __future__ import annotations

import json
import random
from datetime import timedelta
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from socmint.core.db import session_scope
from socmint.core.models import utcnow
from socmint.core.schemas import FactCheckIn
from socmint.core.settings import PROJECT_ROOT
from socmint.ingest.store import store_factchecks, store_symbols

FIXTURES = PROJECT_ROOT / "data" / "fixtures"
DEMO_SYMBOLS = [
    {"symbol": "DEMOPUMP", "name": "Demo Pump Industries Ltd (fictional)", "series": "EQ", "isin": "", "large_cap": False,
     "source": "fixture", "synthetic": True},
    {"symbol": "DEMOSTEADY", "name": "Demo Steady Manufacturing Ltd (fictional)", "series": "EQ", "isin": "",
     "large_cap": False, "source": "fixture", "synthetic": True},
    {"symbol": "DEMOLARGE", "name": "Demo Large Cap Corp (fictional)", "series": "EQ", "isin": "", "large_cap": True,
     "source": "fixture", "synthetic": True},
]


def load_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def tipline_messages() -> list[dict[str, Any]]:
    return load_json("tipline_messages.json")


# ---------------------------------------------------------------------------------- images
def _snake_image(seed: int = 7, size: tuple[int, int] = (640, 480)) -> Image.Image:
    """A deterministic synthetic 'giant snake in a river' picture (abstract shapes, clearly artificial)."""
    rnd = random.Random(seed)
    img = Image.new("RGB", size, (70, 120, 160))
    d = ImageDraw.Draw(img)
    for y in range(0, size[1], 6):  # river ripples
        d.line([(0, y), (size[0], y + rnd.randint(-4, 4))], fill=(60 + rnd.randint(0, 30), 110, 150 + rnd.randint(0, 40)), width=2)
    d.rectangle([0, 0, size[0], 110], fill=(90, 150, 80))  # far bank
    pts = [(40 + i * 28, 260 + int(70 * __import__("math").sin(i / 2.2))) for i in range(21)]
    d.line(pts, fill=(40, 70, 20), width=38, joint="curve")  # the "snake"
    d.ellipse([pts[-1][0] - 30, pts[-1][1] - 26, pts[-1][0] + 34, pts[-1][1] + 26], fill=(35, 60, 18))
    d.text((12, size[1] - 24), "SYNTHETIC DEMO IMAGE", fill=(255, 255, 255))
    return img.filter(ImageFilter.SMOOTH)


def ensure_images() -> dict[str, Path]:
    """Create the repeated-image fixtures if missing: original + a re-compressed, resized 'forward'."""
    media = FIXTURES / "media"
    media.mkdir(parents=True, exist_ok=True)
    original = media / "giant_snake_original.png"
    forwarded = media / "giant_snake_forwarded.jpg"
    unrelated = media / "unrelated_demo.png"
    if not original.exists():
        _snake_image().save(original)
    if not forwarded.exists():
        with Image.open(original) as im:
            fw = ImageEnhance.Brightness(im.resize((480, 360))).enhance(1.08)
            fw.save(forwarded, quality=55)
    if not unrelated.exists():
        img = Image.new("RGB", (400, 300), (230, 220, 200))
        d = ImageDraw.Draw(img)
        for i in range(12):
            d.rectangle([20 + i * 30, 40 + (i % 3) * 60, 40 + i * 30, 260], fill=(200 - i * 12, 80 + i * 10, 60))
        d.text((10, 280), "SYNTHETIC DEMO IMAGE 2", fill=(0, 0, 0))
        img.save(unrelated)
    return {"original": original, "forwarded": forwarded, "unrelated": unrelated}


# ---------------------------------------------------------------------------------- loaders
def load_factchecks() -> int:
    from socmint.understand.media import phash_file

    now = utcnow()
    rows = []
    imgs = ensure_images()
    for fc in load_json("factchecks.json"):
        hashes = []
        if fc.get("image_fixture"):
            p = FIXTURES / fc["image_fixture"]
            if not p.exists():
                p = imgs["original"]
            hashes.append(phash_file(p))
        rows.append(FactCheckIn(publisher=fc["publisher"], title=fc["title"], url=fc["url"], claim_text=fc["claim_text"],
                                rating=fc.get("rating"), lang=fc["lang"], source="fixture", synthetic=True,
                                published_at=now - timedelta(days=fc.get("days_ago", 30)), image_hashes=hashes))
    with session_scope() as s:
        return store_factchecks(s, rows)


def load_symbols() -> int:
    with session_scope() as s:
        n = store_symbols(s, DEMO_SYMBOLS)
    from socmint.understand.entities import clear_caches

    clear_caches()
    return n


def factcheck_url(key: str) -> str:
    return next(fc["url"] for fc in load_json("factchecks.json") if fc["key"] == key)


# ---------------------------------------------------------------------------------- public-channel items
_NARRATIVES = {
    # resurfacing debunked narrative (matches fc_snake): burst in the last 24h
    "snake": [
        "Giant 40 foot snake spotted in the Demo river near the old bridge, villagers scared",
        "Huge snake seen in Demo river today, photo going viral, stay away from the river bank",
        "डेमो नदी में 40 फुट का विशाल सांप दिखा, गांव वाले डरे हुए हैं",
        "Viral photo of a giant snake in Demo river, is this real? 40 foot long",
        "डेमो नदीत 40 फूट लांब मोठा साप दिसला, नदीकाठी जाऊ नका",
        "Massive snake in the Demo river caught on camera this morning near the bridge",
    ],
    # steady narrative (matches fc_bus_mr)
    "bus": [
        "Bus travel in Demo City will be free for everyone from the 1st, municipal corporation announcement",
        "डेमो सिटीमध्ये 1 तारखेपासून सर्व बस प्रवास मोफत होणार, महानगरपालिकेची घोषणा",
        "Free bus rides for all in Demo City starting next month, share with everyone",
        "डेमो सिटी बस प्रवास मोफत होणार असा संदेश, खरं आहे का?",
    ],
    # new narrative without a fact-check
    "water": [
        "Demonagar water supply will be cut for 5 days from tomorrow, store water now",
        "डेमोनगर में कल से 5 दिन पानी की सप्लाई बंद रहेगी, पानी भर लें",
        "Rumour: no water in Demonagar for five days starting tomorrow, store water",
        "डेमोनगरमध्ये उद्यापासून 5 दिवस पाणीपुरवठा बंद राहणार, पाणी भरून ठेवा",
    ],
}
_NOISE = [
    "Cricket: Demo XI won the local tournament final by 20 runs", "Traffic jam on Demo highway due to repairs",
    "मौसम विभाग: डेमो जिले में हल्की बारिश की संभावना", "डेमो शहरात पुस्तक प्रदर्शन सुरू झाले",
    "New library opened in Demo Nagar ward 5", "Demo college admission list released today",
    "डेमो बाजार में सब्जियों के दाम स्थिर", "Power maintenance work in Demo sector 12 on Sunday morning",
    "Demo marathon registrations open till Friday", "डेमो गावात आरोग्य शिबिराचे आयोजन",
]
_COORD = "BREAKING: Demonagar dam has cracked, evacuate now! Share before this is deleted #DemoDam"
_PUMP_PROMO = [
    "$DEMOPUMP sure shot multibagger, target 500 in 7 days! Join VIP group fast",
    "DEMOPUMP शेयर जल्दी खरीदो, अपर सर्किट पक्का, टारगेट ₹500",
    "Operator buying in DEMOPUMP stock, insider tip, buy before Monday #DEMOPUMP",
    "DEMOPUMP शेअर लगेच खरेदी करा, पैसे दुप्पट होणार",
    "#DEMOPUMP jackpot stock, sure shot 3x, SEBI registered INH999000777 analyst call",
]


def demo_items(now=None) -> list[dict[str, Any]]:
    """Deterministic synthetic public-channel items (Telegram/Reddit/YouTube-like) for all batch modules."""
    from datetime import datetime  # noqa: F401  (type only)

    rnd = random.Random(42)
    now = now or utcnow()
    items: list[dict[str, Any]] = []

    def add(text: str, when, channel: str, account: str, stype: str, tag: str) -> None:
        items.append({"source": "fixture_public", "source_type": stype, "external_id": f"{tag}:{len(items)}",
                      "url": f"https://social.example/{channel}/{len(items)}", "channel": channel, "author_handle": account,
                      "published_at": when, "text": text, "synthetic": True, "meta": {"fixture": tag}})

    channels = ["demo_news_tg", "r/demo_city", "demo_yt_comments", "demo_alerts_tg"]
    types = {"demo_news_tg": "telegram", "r/demo_city": "reddit", "demo_yt_comments": "youtube", "demo_alerts_tg": "telegram"}
    # snake: 3 in previous 72h, 14 in last 24h
    for i in range(17):
        when = now - (timedelta(hours=rnd.uniform(25, 96)) if i < 3 else timedelta(hours=rnd.uniform(0.5, 23)))
        ch = rnd.choice(channels)
        add(_NARRATIVES["snake"][i % 6], when, ch, f"acct_{rnd.randint(1, 60):02d}", types[ch], "narr_snake")
    for i in range(12):  # bus: steady over 6 days
        ch = rnd.choice(channels)
        add(_NARRATIVES["bus"][i % 4], now - timedelta(hours=rnd.uniform(1, 144)), ch, f"acct_{rnd.randint(1, 60):02d}",
            types[ch], "narr_bus")
    for i in range(10):  # water: steady over 4 days
        ch = rnd.choice(channels)
        add(_NARRATIVES["water"][i % 4], now - timedelta(hours=rnd.uniform(1, 96)), ch, f"acct_{rnd.randint(1, 60):02d}",
            types[ch], "narr_water")
    for i, t in enumerate(_NOISE * 2):
        ch = rnd.choice(channels)
        add(t if i < len(_NOISE) else t + " (update)", now - timedelta(hours=rnd.uniform(1, 200)), ch,
            f"acct_{rnd.randint(1, 60):02d}", types[ch], "noise")
    # coordinated set: 8 distinct accounts, 3 channels, within 6 minutes, ~3h ago
    base = now - timedelta(hours=3)
    for i in range(8):
        ch = ["demo_news_tg", "demo_alerts_tg", "r/demo_city"][i % 3]
        suffix = ["", " 🙏", " !!", "", " #share", "", " 🙏🙏", " pls share"][i]
        add(_COORD + suffix, base + timedelta(seconds=rnd.randint(0, 360)), ch, f"coord_acct_{i + 1}", types[ch], "coord")
    # control: same text by 3 accounts spread over 3 days (organic resharing)
    for i in range(3):
        add("Old news: Demo bridge repair will finish by December says engineer", now - timedelta(days=i + 1, hours=i * 5),
            channels[i], f"organic_{i + 1}", types[channels[i]], "coord_control")
    # market: DEMOPUMP baseline 0-2 mentions/day for 30 days, then a spike today
    for d in range(30, 0, -1):
        for _ in range(rnd.choice([0, 1, 1, 2])):
            add("Anyone tracking DEMOPUMP stock results this quarter?", now - timedelta(days=d, hours=rnd.uniform(0, 10)),
                "r/demo_stocks", f"acct_{rnd.randint(1, 60):02d}", "reddit", "mkt_base")
        if rnd.random() < 0.5:
            add("DEMOLARGE share price steady this week, NSE update", now - timedelta(days=d, hours=2), "r/demo_stocks",
                f"acct_{rnd.randint(1, 60):02d}", "reddit", "mkt_base_large")
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    since_midnight = max((now - day_start).total_seconds(), 600.0)  # spike stays inside today's (UTC) market day
    for i in range(40):
        ch = rnd.choice(["demo_tips_tg", "demo_alerts_tg", "demo_yt_comments"])
        when = day_start + timedelta(seconds=since_midnight * rnd.uniform(0.02, 0.98))
        add(_PUMP_PROMO[i % 5] + ("" if i < 5 else f" #{i}"), when, ch,
            f"promo_{rnd.randint(1, 25):02d}", types.get(ch, "telegram"), "mkt_spike")
    return items


def demo_market(now=None) -> list[dict[str, Any]]:
    """31 trading days of synthetic bhavcopy rows for the demo symbols (DEMOPUMP pumped today)."""
    rnd = random.Random(7)
    now = now or utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = []
    prices = {"DEMOPUMP": 42.0, "DEMOSTEADY": 310.0, "DEMOLARGE": 2450.0}
    for d in range(30, -1, -1):
        day = today - timedelta(days=d)
        for sym, base_vol in (("DEMOPUMP", 60_000), ("DEMOSTEADY", 200_000), ("DEMOLARGE", 3_000_000)):
            vol = base_vol * rnd.uniform(0.8, 1.2)
            chg = rnd.uniform(-0.015, 0.015)
            if d == 0 and sym == "DEMOPUMP":
                vol, chg = base_vol * 11, 0.19
            if d == 0 and sym == "DEMOSTEADY":
                vol, chg = base_vol * 6, 0.02   # volume spike without chatter: must NOT alert
            prices[sym] *= 1 + chg
            rows.append({"symbol": sym, "date": day, "close": round(prices[sym], 2), "volume": round(vol),
                         "turnover": round(vol * prices[sym], 2), "synthetic": True})
    return rows


def load_items(now=None) -> int:
    from socmint.core.schemas import ItemIn
    from socmint.ingest.store import store_items

    with session_scope() as s:
        return store_items(s, [ItemIn(**i) for i in demo_items(now)])


def load_market(now=None) -> int:
    from socmint.core.schemas import MarketIn
    from socmint.ingest.store import store_market

    with session_scope() as s:
        return store_market(s, [MarketIn(**r) for r in demo_market(now)])


def load_lookalikes() -> int:
    """The demo brand's fictional registered lookalike (as a dnstwist scan would record it)."""
    from socmint.analyze.impersonation import record_lookalike

    n = 0
    for dom, fuzzer, a in (("dem0bank.example", "homoglyph", ["192.0.2.10"]),
                           ("demobank-kyc-update.example", "dictionary", ["192.0.2.11"]),
                           ("demobnak.example", "transposition", [])):
        n += record_lookalike("demobank", dom, f"fixture:dnstwist:{fuzzer}", registered=bool(a), dns_a=a,
                              evidence={"fuzzer": fuzzer, "official": "demobank.example", "note": "synthetic fixture; "
                                        "192.0.2.0/24 is the RFC 5737 documentation range"}, synthetic=True)
    return n
