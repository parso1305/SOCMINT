"""Synthetic showcase scenario: three investigations that exercise every part of the showcase dashboard.

1. Event  "Pune floods 2026 (demo)": ~300 posts across Bluesky, Mastodon, Telegram, Reddit, YouTube and news in
   en/hi/mr/Hinglish over 5 days (rain peak ~2.5 days ago), with an old-video "dam has burst" rumour that matches a
   synthetic fact-check, a fake relief appeal (UPI ID + DemoBank lookalike domain), 8 accounts posting near-identical
   text within 6 minutes, and ordinary civic-safety updates.
2. Subject "Demo: stock-tip channel": a fictional Telegram channel + linked YouTube channel pushing DEMOPUMP, lined up
   with the synthetic NSE volume spike so the market-risk alert fires.
3. Group promoted from the event's top possible-coordination group.

Everything is fictional (invented names, handles, numeric ids, `.example` domains, a fictional dam) and every row is
`synthetic=True`. The posts reach the database through the real `investigate.runner.collect()` path: `FixtureSource`
stands in for the platform collectors, so the same matching, linking, enrichment and analysis code runs offline.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any

from socmint.core.models import utcnow
from socmint.core.schemas import AccountIn, FactCheckIn, FetchResult, ItemIn
from socmint.ingest.base import OptedOut, Source

EVENT_NAME = "Pune floods 2026 (demo)"
SUBJECT_NAME = "Demo: stock-tip channel"
GROUP_NAME = "Demo: dam-rumour copy network"
DAM = "Demosagar dam"  # fictional
AREAS = ["Sinhagad road", "Kothrud", "Hadapsar", "Baner", "Wakad", "Shivajinagar", "Yerawada", "Katraj", "Aundh",
         "Viman Nagar", "Deccan", "Warje"]
EVENT_TERMS = {"en": ["Pune flood", "Pune floods", "Pune rains", "#PuneRains", DAM],
               "hi": ["पुणे बाढ़", "बाढ़", "डेमोसागर"], "mr": ["पुणे पूर", "पूर", "डेमोसागर"],
               "hi-Latn": ["Pune baarish", "Pune flood"]}
EVENT_SOURCES = ["bluesky", "mastodon", "telegram", "reddit", "youtube", "news_rss"]
SCAM_UPI = "punerelief.help@okdemo"
SCAM_DOMAIN = "demobank-relief.example"
TIP_CHANNEL = "multibagger_tips_demo"
TIP_YT = "@multibaggertipsdemo"

FACTCHECKS = [
    {"publisher": "Demo Fact Check Desk (synthetic)", "lang": "en",
     "url": "https://factcheck.example/en/demosagar-dam-old-video",
     "title": "Fact Check: Video of 'Demosagar dam burst' is from 2019; the dam is safe",
     "claim_text": "An old 2019 video is being shared with the claim that Demosagar dam near Pune has burst and flood "
                   "water is entering the city. Officials say the dam is safe.",
     "rating": "False", "days_ago": 1},
    {"publisher": "डेमो फ़ैक्ट चेक (synthetic)", "lang": "hi",
     "url": "https://factcheck.example/hi/demosagar-bandh-purana-video",
     "title": "फ़ैक्ट चेक: डेमोसागर बांध टूटने का वीडियो 2019 का है",
     "claim_text": "वायरल वीडियो में दावा किया जा रहा है कि पुणे के पास डेमोसागर बांध टूट गया है। यह वीडियो 2019 का है, बांध सुरक्षित है।",
     "rating": "गलत", "days_ago": 1},
]

# ------------------------------------------------------------------------------------------- text pools
_CIVIC = {
    "en": ["Heavy rain in {a}, waterlogging near the main road. Avoid travel if you can #PuneRains",
           "NDRF rescue teams deployed in {a} as the Pune flood situation worsens. Helpline 1916 is active",
           "Pune rains: schools in {a} closed tomorrow, says the district administration",
           "Traffic diverted at {a} because of Pune flood water, use alternate routes #PuneRains",
           "Power cut in {a} since morning, Pune rains causing outages across the ward",
           "Relief camp opened at the municipal school in {a} for families hit by the Pune floods",
           "IMD red alert for Pune district. Rainfall in {a} crossed 120 mm since last night #PuneRains",
           "Water entered ground floor shops in {a}. Pune flood update from residents"],
    "hi": ["{a} में भारी बारिश, पुणे बाढ़ जैसे हालात, घर से बाहर न निकलें",
           "पुणे बाढ़: {a} में राहत शिविर खोला गया, हेल्पलाइन 1916 पर संपर्क करें",
           "{a} की सड़कें पानी में डूबीं, बाढ़ के कारण यातायात बंद",
           "पुणे बाढ़ अपडेट: {a} में एनडीआरएफ की टीम बचाव कार्य में लगी"],
    "mr": ["{a} परिसरात मुसळधार पाऊस, पुणे पूर परिस्थिती, काळजी घ्या",
           "पुणे पूर: {a} मध्ये रस्ते पाण्याखाली, वाहतूक बंद",
           "{a} भागात पूर, नागरिकांचे स्थलांतर सुरू, मदत छावणी उघडली",
           "पुणे पूर अपडेट: {a} मध्ये शाळांना सुट्टी जाहीर"],
    "hi-Latn": ["{a} mein bahut paani bhar gaya hai, Pune baarish ne sab rok diya #PuneRains",
                "Pune flood update: {a} ka road band hai, ghar pe hi raho sab",
                "{a} mein light nahi hai subah se, Pune baarish bahut tez hai"],
}
_EXTRA = ["", "", " Stay safe everyone.", " Photos from 7 pm.", " Please share with family.", " Update at 9:30 am.",
          " Thanks to the volunteers.", " Water level still rising.", " Situation better than yesterday."]
_RUMOUR = {
    "en": [f"{DAM} has burst! Video from this morning, flood water entering Pune city, evacuate now",
           f"Is it true {DAM} has burst? This video is going viral, Pune flood getting worse",
           f"Video: {DAM} burst near Pune, water rushing towards Sinhagad road. Leave your homes",
           f"Watch: {DAM} wall broke this morning, Pune floods will be worse than 2019"],
    "hi": ["डेमोसागर बांध टूट गया, वीडियो देखिए, पुणे में बाढ़ का पानी आ रहा है, तुरंत घर खाली करें",
           "डेमोसागर बांध फट गया है, यह वीडियो आज सुबह का है, पुणे बाढ़"],
    "mr": ["डेमोसागर धरण फुटले, हा व्हिडिओ पहा, पुणे पूर वाढणार, लगेच घर सोडा",
           "डेमोसागर धरण फुटलं असा व्हिडिओ फिरतोय, पुणे पूर, खरं आहे का?"],
}
_CORRECTION = [f"Officials: {DAM} is safe, the viral 'dam burst' video is an old video from 2019 #PuneRains",
               "डेमोसागर बांध सुरक्षित है, वायरल वीडियो पुराना वीडियो है, अफवाह पर ध्यान न दें",
               "डेमोसागर धरण सुरक्षित, व्हायरल व्हिडिओ जुना व्हिडिओ आहे, अफवा पसरवू नका"]
_SCAM = [f"URGENT Pune flood relief: DemoBank official relief fund. Donate now to UPI {SCAM_UPI} or visit "
         f"{SCAM_DOMAIN}. Every Rs 500 feeds a family #PuneRains",
         f"पुणे बाढ़ राहत कोष: DemoBank की आधिकारिक अपील, तुरंत दान करें UPI {SCAM_UPI} या {SCAM_DOMAIN} पर जाएं",
         f"Pune flood victims need help NOW. DemoBank verified donation link {SCAM_DOMAIN} or UPI {SCAM_UPI}, "
         "limited time, pay immediately",
         f"Pune flood relief ke liye DemoBank fund mein abhi donate karo, UPI {SCAM_UPI}, link {SCAM_DOMAIN} jaldi"]
_COORD = (f"ALERT: {DAM} gates have failed, Pune flood water will reach Sinhagad road within one hour. "
          "Share before this is deleted #PuneRains")
_COORD_SUFFIX = ["", " 🙏", " !!", "", " #share", "", " 🙏🙏", " pls share"]
_NEWS = [("Pune floods: NDRF deploys teams as rain continues", "Rescue teams were deployed in low-lying areas of Pune "
          "after heavy overnight rain; the district administration opened relief camps."),
         ("Pune rains: schools shut, traffic diverted across the city", "Several roads were closed and buses diverted "
          "as Pune flood water entered residential areas."),
         (f"Fact check: viral '{DAM} burst' video is old", f"Officials said {DAM} is safe and the video being "
          "shared is an old video from 2019."),
         ("पुणे बाढ़: निचले इलाकों से लोगों को निकाला गया", "भारी बारिश के बाद पुणे में बाढ़ जैसे हालात, प्रशासन ने राहत शिविर खोले।"),
         ("पुणे पूर: धरणातून विसर्ग वाढवला, नदीकाठच्या नागरिकांना सतर्कतेचा इशारा", "मुसळधार पावसामुळे पुणे पूर परिस्थिती गंभीर."),
         ("Police warn of fake Pune flood relief appeals", "Police said a donation appeal using a lookalike bank "
          "domain is not genuine and urged citizens to donate only through official channels.")]

# ------------------------------------------------------------------------------------------- fictional accounts
_FIRST = ["rhea", "omkar", "sana", "kabir", "meera", "aditya", "tanvi", "rohan", "isha", "nikhil", "pooja", "arjun",
          "kavya", "siddharth", "neha", "varun", "ananya", "yash", "riya", "pranav", "gauri", "harsh", "diya", "manav"]
PLATFORM_SHARE = [("bluesky", 0.30), ("mastodon", 0.22), ("telegram", 0.18), ("reddit", 0.15), ("youtube", 0.15)]


def _account(platform: str, n: int, tag: str = "") -> dict[str, Any]:
    """A fictional account on `platform` (deterministic from `n`)."""
    first = _FIRST[n % len(_FIRST)]
    stem = f"{first}{tag}{n:02d}"
    if platform == "bluesky":
        return {"author_id": f"did:plc:demo{stem}", "author_handle": f"{stem}.demo-bsky.example",
                "channel": None, "url": f"https://bsky.example/profile/{stem}"}
    if platform == "mastodon":
        return {"author_id": f"{stem}@mastodon.example", "author_handle": f"{stem}@mastodon.example",
                "channel": None, "url": f"https://mastodon.example/@{stem}"}
    if platform == "telegram":
        cid = str(-1009000000000 - n * 7 - len(tag))
        return {"author_id": cid, "author_handle": f"{stem}_updates_demo", "channel": f"{stem}_updates_demo",
                "url": f"https://t.example/{stem}_updates_demo"}
    if platform == "reddit":
        return {"author_id": f"u_{stem}_demo", "author_handle": f"u_{stem}_demo", "channel": "r/pune_demo",
                "url": f"https://reddit.example/user/u_{stem}_demo"}
    return {"author_id": f"UCdemo{stem.upper()}", "author_handle": f"@{stem}demo", "channel": f"@{stem}demo",
            "url": f"https://youtube.example/@{stem}demo"}


def _display(platform: str, acc: dict[str, Any]) -> str:
    base = (acc["author_handle"] or "").split(".")[0].split("@")[0].replace("_", " ").strip() or "demo"
    return f"{base.title()} (fictional)"


def _pick_platform(rnd: random.Random) -> str:
    x, acc = rnd.random(), 0.0
    for p, w in PLATFORM_SHARE:
        acc += w
        if x < acc:
            return p
    return PLATFORM_SHARE[-1][0]


def _hours_ago(rnd: random.Random) -> float:
    """Rain peak ~60 h ago, tail into the last 24 h, nothing older than 5 days."""
    return min(max(rnd.gauss(60, 22), 0.5), 119.0)


# ------------------------------------------------------------------------------------------- event posts
def event_posts(now: datetime | None = None) -> list[ItemIn]:
    rnd = random.Random(2026)
    now = now or utcnow()
    out: list[ItemIn] = []
    civic_hub = {p: _account(p, 0, "civic") for p, _ in PLATFORM_SHARE}  # a fictional civic-updates account per platform

    def add(platform: str, acc: dict[str, Any], text: str, when: datetime, lang: str, tag: str,
            **kw: Any) -> ItemIn:
        n = len(out)
        hashtags = sorted({w.lstrip("#") for w in text.split() if w.startswith("#")})
        it = ItemIn(source=f"demo_{platform}", source_type=platform,
                    external_id=f"pune:{n}", url=f"{acc['url']}/post/{n}", channel=acc["channel"],
                    author_id=acc["author_id"], author_handle=acc["author_handle"],
                    author_display_name=_display(platform, acc), published_at=when, lang=lang, text=text,
                    hashtags=hashtags, synthetic=True,
                    engagement={"likes": rnd.randint(0, 400), "reposts": rnd.randint(0, 120),
                                "replies": rnd.randint(0, 40)},
                    meta={"fixture": tag, **kw.pop("meta", {})}, **kw)
        out.append(it)
        return it

    # ordinary civic-safety updates (~190)
    for i in range(190):
        p = _pick_platform(rnd)
        lang = rnd.choices(["en", "hi", "mr", "hi-Latn"], weights=[45, 20, 22, 13])[0]
        acc = civic_hub[p] if i % 9 == 0 else _account(p, rnd.randint(1, 60))
        text = rnd.choice(_CIVIC[lang]).format(a=rnd.choice(AREAS)) + (rnd.choice(_EXTRA) if lang == "en" else "")
        kw: dict[str, Any] = {}
        hub = civic_hub[p]
        if p in ("bluesky", "mastodon") and i % 5 == 1 and acc is not hub:  # replies to / mentions of the civic hub
            kw = {"mentions": ["@" + hub["author_handle"]], "reply_to_external_id": "pune:0",
                  "meta": {"mention_ids": [hub["author_id"]], "reply_to_author_id": hub["author_id"]}}
        if p == "telegram" and i % 4 == 2 and acc is not hub:  # channel forwards from the civic hub channel
            kw = {"forwarded_from": hub["author_id"]}
        add(p, acc, text, now - timedelta(hours=_hours_ago(rnd)), "hi" if lang == "hi-Latn" else lang, "civic", **kw)

    # old-video "dam has burst" rumour: a few early posts, most in the last 24 h (growing narrative)
    video = {"type": "video", "url": "https://video.example/demosagar-2019.mp4"}
    for i in range(34):
        lang = rnd.choices(["en", "hi", "mr"], weights=[50, 25, 25])[0]
        hrs = rnd.uniform(26, 90) if i < 6 else rnd.uniform(0.5, 23.5)
        p = _pick_platform(rnd)
        add(p, _account(p, rnd.randint(61, 110), "r"), rnd.choice(_RUMOUR[lang]), now - timedelta(hours=hrs), lang,
            "rumour", media=[video], urls=["https://video.example/demosagar-2019.mp4"])
    for i, t in enumerate(_CORRECTION):  # corrections from the civic hubs
        p = ["bluesky", "mastodon", "telegram"][i]
        add(p, civic_hub[p], t, now - timedelta(hours=rnd.uniform(2, 18)), ["en", "hi", "mr"][i], "correction")

    # fake relief appeal (UPI ID + DemoBank lookalike domain)
    for i in range(18):
        p = ["telegram", "bluesky", "mastodon", "youtube", "reddit"][i % 5]
        add(p, _account(p, 120 + i % 6, "relief"), _SCAM[i % len(_SCAM)], now - timedelta(hours=rnd.uniform(4, 70)),
            "hi" if i % len(_SCAM) in (1, 3) else "en", "scam", urls=[f"http://{SCAM_DOMAIN}"])

    # 8 accounts, near-identical text, within 6 minutes, across 3 platforms; each also pushes the rumour earlier
    base = now - timedelta(hours=9)
    for i in range(8):
        p = ["bluesky", "mastodon", "telegram"][i % 3]
        acc = _account(p, 200 + i, "x")
        add(p, acc, _COORD + _COORD_SUFFIX[i], base + timedelta(seconds=rnd.randint(0, 360)), "en", "coordinated")
        add(p, acc, _RUMOUR["en"][i % 2] + " " + rnd.choice(["#PuneRains", "!!", "share"]),
            now - timedelta(hours=rnd.uniform(10, 20)), "en", "rumour", media=[video])

    # news (no author account)
    for i in range(27):
        title, summary = _NEWS[i % len(_NEWS)]
        hrs = _hours_ago(rnd) if i % len(_NEWS) not in (2, 5) else rnd.uniform(1, 20)
        n = len(out)
        out.append(ItemIn(source="demo_news_rss", source_type="news", external_id=f"pune:{n}",
                          url=f"https://news.example/pune/{n}", channel="Demo Times (synthetic)",
                          published_at=now - timedelta(hours=hrs), text=f"{title}. {summary}",
                          urls=[f"https://news.example/pune/{n}"], synthetic=True,
                          lang="hi" if "बाढ़" in title else ("mr" if "पूर" in title else "en"),
                          meta={"fixture": "news", "title": title}))
    return out


# ------------------------------------------------------------------------------------------- subject posts
_TIP_BASE = ["Market wrap: Nifty flat today, watchlist for tomorrow posted in the channel",
             "Weekend study: how to read volume charts (educational)", "DEMOPUMP quarterly numbers due next month",
             "Live session tonight at 8 pm on small caps"]
_TIP_PUMP = ["$DEMOPUMP sure shot multibagger, target 500 in 7 days! Join VIP group fast",
             "DEMOPUMP अपर सर्किट पक्का, टारगेट ₹500, मल्टीबैगर स्टॉक #DEMOPUMP",
             "#DEMOPUMP jackpot stock, sure shot 3x, SEBI registered INH999000777 analyst call, VIP group only",
             "DEMOPUMP शेअर मल्टीबॅगर, पैसे दुप्पट होणार, टार्गेट ₹500",
             "DEMOPUMP upper circuit again tomorrow, pakka profit, premium group link in bio"]
TIP_ACCOUNTS = {
    "telegram": AccountIn(platform="telegram", platform_id="-1009990001001", handle=TIP_CHANNEL,
                          display_name="Multibagger Tips (fictional demo channel)", url=f"https://t.example/{TIP_CHANNEL}",
                          followers=48210, posts_count=1312, created_at=datetime(2024, 3, 2),
                          description="Daily stock tips. VIP group available. (fictional)", synthetic=True),
    "youtube": AccountIn(platform="youtube", platform_id="UCdemoMULTIBAGGERTIPS01", handle=TIP_YT,
                         display_name="Multibagger Tips Live (fictional demo)", url="https://youtube.example/@multibaggertipsdemo",
                         followers=15400, posts_count=212, created_at=datetime(2024, 5, 19),
                         description="Stock market live sessions (fictional)", synthetic=True),
}


def subject_posts(now: datetime | None = None) -> dict[str, list[ItemIn]]:
    """Posts of the fictional stock-tip channel (Telegram) and its linked YouTube channel."""
    rnd = random.Random(77)
    now = now or utcnow()
    out: dict[str, list[ItemIn]] = {"telegram": [], "youtube": []}

    def add(platform: str, text: str, when: datetime, tag: str) -> None:
        a = TIP_ACCOUNTS[platform]
        n = sum(len(v) for v in out.values())
        out[platform].append(ItemIn(
            source=f"demo_{platform}", source_type=platform, external_id=f"tips:{n}", url=f"{a.url}/{n}",
            channel=a.handle, author_id=a.platform_id, author_handle=a.handle, author_display_name=a.display_name,
            published_at=when, text=text, synthetic=True, hashtags=["DEMOPUMP"] if "#DEMOPUMP" in text else [],
            engagement={"views": rnd.randint(2000, 40000), "forwards": rnd.randint(0, 600)}, meta={"fixture": tag}))

    for d in range(29, 0, -1):  # quiet baseline: one or two posts a day, DEMOPUMP rarely
        for _ in range(rnd.choice([1, 1, 2])):
            add("telegram", rnd.choice(_TIP_BASE), now - timedelta(days=d, hours=rnd.uniform(0, 12)), "tip_base")
        if d % 6 == 0:
            add("youtube", "Live: small cap watchlist and chart reading (educational)",
                now - timedelta(days=d, hours=3), "tip_base")
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    since_midnight = max((now - day_start).total_seconds(), 600.0)  # the spike stays inside today's (UTC) market day
    for i in range(16):
        plat = "youtube" if i % 4 == 3 else "telegram"
        when = min(day_start + timedelta(seconds=since_midnight * rnd.uniform(0.05, 0.95)), now)
        add(plat, _TIP_PUMP[i % len(_TIP_PUMP)] + ("" if i < 5 else f" ({i})"), when, "tip_pump")
    return out


# ------------------------------------------------------------------------------------------- fixture source
class FixtureSource(Source):
    """Offline stand-in for a platform collector, serving the scenario's synthetic posts."""

    type_name = "fixture_scenario"
    capabilities = {"terms", "accounts"}

    def __init__(self, cfg: dict[str, Any], platform: str, event: list[ItemIn], subject: dict[str, list[ItemIn]]):
        super().__init__(cfg)
        self.platform, self._event, self._subject = platform, event, subject

    def fetch(self, since):
        return FetchResult()

    def search_terms(self, terms_by_lang, since, limit):
        items = [it.model_copy(deep=True) for it in self._event
                 if it.source_type == self.platform and (it.published_at is None or it.published_at >= since)]
        profiles = {}
        for it in items:
            if it.author_id and it.author_id not in profiles:
                h = sum(map(ord, it.author_id))
                profiles[it.author_id] = AccountIn(
                    platform=it.source_type, platform_id=it.author_id, handle=it.author_handle,
                    display_name=it.author_display_name, url=it.url.rsplit("/post/", 1)[0] if it.url else None,
                    followers=40 + h * 37 % 9000, following=10 + h % 400, posts_count=50 + h * 13 % 4000,
                    created_at=datetime(2019 + h % 6, 1 + h % 12, 1 + h % 27), synthetic=True)
        return FetchResult(items=items[:limit], accounts=list(profiles.values()),
                           notes=[f"synthetic fixture ({self.platform})"])

    def _profile(self, handle: str) -> AccountIn:
        tip = TIP_ACCOUNTS.get(self.platform)
        if tip and handle in (tip.handle, tip.platform_id):
            return tip.model_copy()
        it = next((i for i in self._event if i.source_type == self.platform and handle in (i.author_handle, i.author_id)),
                  None)
        if it is None:
            raise OptedOut(f"{handle} is not a public account in the demo fixtures")
        return AccountIn(platform=self.platform, platform_id=it.author_id or handle, handle=it.author_handle,
                         display_name=it.author_display_name, synthetic=True,
                         followers=120 + len(handle) * 11, posts_count=300 + len(handle) * 7)

    def account_profile(self, handle):
        return self._profile(handle)

    def account_posts(self, handle, since, limit):
        prof = self._profile(handle)
        pool = self._subject.get(self.platform, []) + self._event
        items = [it.model_copy(deep=True) for it in pool if it.source_type == self.platform
                 and it.author_id == prof.platform_id and (it.published_at is None or it.published_at >= since)]
        return FetchResult(items=items[:limit], accounts=[prof])


def fixture_factory(now: datetime | None = None):
    """(factory, resolver) for `runner.collect(..., factory=, resolver=)`: one fixture source per logical source."""
    event, subject = event_posts(now), subject_posts(now)
    platform_of = {"news_rss": "news", "telegram": "telegram"}

    def factory(logical: str, cfg: dict[str, Any]) -> Source:
        return FixtureSource({**cfg, "name": cfg["name"]}, platform_of.get(logical, logical), event, subject)

    def resolver(logical: str) -> list[dict[str, Any]]:
        return [{"name": f"demo_{logical}", "type": "fixture_scenario"}]

    return factory, resolver


def factchecks(now: datetime | None = None) -> list[FactCheckIn]:
    now = now or utcnow()
    return [FactCheckIn(publisher=f["publisher"], title=f["title"], url=f["url"], claim_text=f["claim_text"],
                        rating=f["rating"], lang=f["lang"], source="fixture", synthetic=True,
                        published_at=now - timedelta(days=f["days_ago"])) for f in FACTCHECKS]
