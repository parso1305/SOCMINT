"""Citizen reply templates (en / hi / mr).

Rules enforced here:
- Only the four verdict families exist; there is no "true / verified" wording anywhere.
- Every reply ends with the disclaimer; scam replies add how to report fraud (1930, cybercrime.gov.in).
NOTE: Hindi/Marathi strings were drafted for the prototype and should be reviewed by native speakers.
"""

from __future__ import annotations

from socmint.core.schemas import CheckResult, Verdict

VERDICT_LINE = {
    "en": {
        Verdict.FACTCHECK_MATCH: "🔎 This matches a claim that fact-checkers have already examined.",
        Verdict.SEEN_BEFORE: "🖼️ This image/video has been seen before, in an older or different context.",
        Verdict.LIKELY_SCAM: "⚠️ This looks like a LIKELY SCAM.",
        Verdict.SUSPICIOUS: "⚠️ This message looks SUSPICIOUS.",
        Verdict.NOT_VERIFIED: "⏳ We could not verify this yet. It has been queued for a human reviewer.",
    },
    "hi": {
        Verdict.FACTCHECK_MATCH: "🔎 यह दावा पहले ही फ़ैक्ट-चेकर्स द्वारा जांचा जा चुका है।",
        Verdict.SEEN_BEFORE: "🖼️ यह फ़ोटो/वीडियो पहले भी किसी पुराने या अलग संदर्भ में देखा गया है।",
        Verdict.LIKELY_SCAM: "⚠️ यह संभवतः एक धोखाधड़ी (स्कैम) है।",
        Verdict.SUSPICIOUS: "⚠️ यह संदेश संदिग्ध लग रहा है।",
        Verdict.NOT_VERIFIED: "⏳ हम अभी इसकी पुष्टि नहीं कर सके। इसे समीक्षा के लिए कतार में रखा गया है।",
    },
    "mr": {
        Verdict.FACTCHECK_MATCH: "🔎 हा दावा फॅक्ट-चेकर्सनी आधीच तपासलेला आहे.",
        Verdict.SEEN_BEFORE: "🖼️ हा फोटो/व्हिडिओ याआधी जुन्या किंवा वेगळ्या संदर्भात दिसला आहे.",
        Verdict.LIKELY_SCAM: "⚠️ हा बहुधा फसवणुकीचा (स्कॅम) संदेश आहे.",
        Verdict.SUSPICIOUS: "⚠️ हा संदेश संशयास्पद वाटतो.",
        Verdict.NOT_VERIFIED: "⏳ आम्ही अद्याप याची पडताळणी करू शकलो नाही. हा संदेश पुनरावलोकनासाठी रांगेत ठेवला आहे.",
    },
}

REASON_TEXT = {
    "en": {
        "category:kyc_block": "Threatens to block your account / asks for KYC update",
        "category:digital_arrest": "Uses police / CBI / customs / 'digital arrest' threats",
        "category:investment": "Promises guaranteed or very high investment returns",
        "category:task_job": "Offers easy part-time / task-based earnings",
        "category:lottery_prize": "Says you won a lottery, prize or gift",
        "category:fake_customer_care": "Fake customer-care / refund / remote-access app pattern",
        "category:loan_app": "Instant-loan offer pattern",
        "category:utility_disconnection": "Threatens electricity / utility disconnection",
        "category:courier_parcel": "Courier / parcel delivery problem pattern",
        "signal:threat_feed_url": "The link is listed in a public phishing/malware feed",
        "signal:lookalike_domain": "The website imitates an official brand domain",
        "signal:brand_unofficial_contact": "Claims to be {brand} but uses a non-official link or number",
        "signal:shortener": "Uses a short link that hides the real website",
        "signal:new_domain": "The website has not been seen in our sources before",
        "signal:apk_link": "Asks you to install an app file (.apk) from outside the app store",
        "signal:urgency": "Pressures you to act immediately",
        "signal:upi_payment_request": "Asks you to pay money to a UPI ID",
        "signal:safe_browsing_hit": "Google Safe Browsing lists the link as unsafe",
        "signal:link_request": "Asks you to open a link in a scam-pattern message",
        "signal:contact_mobile": "Asks you to call or message a personal mobile number",
        "signal:group_invite": "Pushes you to join a private Telegram/WhatsApp 'tips' group",
        "signal:sebi_claim_unverified": "Claims SEBI registration that we could not find in the registry list",
        "factcheck": "Fact-check: {title} ({publisher}, {date})",
        "seen": "First seen {date}: {context}",
    },
    "hi": {
        "category:kyc_block": "खाता बंद करने की धमकी / केवाईसी अपडेट की मांग",
        "category:digital_arrest": "पुलिस / सीबीआई / कस्टम / 'डिजिटल अरेस्ट' की धमकी",
        "category:investment": "गारंटीड या बहुत ज़्यादा मुनाफ़े का वादा",
        "category:task_job": "आसान पार्ट-टाइम / टास्क से कमाई का ऑफ़र",
        "category:lottery_prize": "लॉटरी, इनाम या गिफ़्ट जीतने का दावा",
        "category:fake_customer_care": "नकली कस्टमर केयर / रिफंड / रिमोट-एक्सेस ऐप का तरीका",
        "category:loan_app": "तुरंत लोन ऑफ़र का तरीका",
        "category:utility_disconnection": "बिजली / सेवा कनेक्शन काटने की धमकी",
        "category:courier_parcel": "कूरियर / पार्सल डिलीवरी समस्या का तरीका",
        "signal:threat_feed_url": "यह लिंक सार्वजनिक फ़िशिंग/मैलवेयर सूची में दर्ज है",
        "signal:lookalike_domain": "वेबसाइट किसी आधिकारिक ब्रांड डोमेन की नकल करती है",
        "signal:brand_unofficial_contact": "{brand} होने का दावा, लेकिन लिंक या नंबर आधिकारिक नहीं है",
        "signal:shortener": "छोटा लिंक असली वेबसाइट छुपाता है",
        "signal:new_domain": "यह वेबसाइट हमारे स्रोतों में पहले नहीं दिखी",
        "signal:apk_link": "ऐप स्टोर के बाहर से ऐप फ़ाइल (.apk) इंस्टॉल करने को कहता है",
        "signal:urgency": "तुरंत कार्रवाई करने का दबाव",
        "signal:upi_payment_request": "किसी UPI ID पर पैसे भेजने को कहता है",
        "signal:safe_browsing_hit": "Google Safe Browsing इस लिंक को असुरक्षित बताता है",
        "signal:link_request": "स्कैम जैसे संदेश में लिंक खोलने को कहता है",
        "signal:contact_mobile": "किसी निजी मोबाइल नंबर पर कॉल या मैसेज करने को कहता है",
        "signal:group_invite": "निजी Telegram/WhatsApp 'टिप्स' ग्रुप जॉइन करने का दबाव",
        "signal:sebi_claim_unverified": "SEBI पंजीकरण का दावा, जो हमारी रजिस्ट्री सूची में नहीं मिला",
        "factcheck": "फ़ैक्ट-चेक: {title} ({publisher}, {date})",
        "seen": "पहली बार देखा गया {date}: {context}",
    },
    "mr": {
        "category:kyc_block": "खाते बंद करण्याची धमकी / केवायसी अपडेटची मागणी",
        "category:digital_arrest": "पोलीस / सीबीआय / कस्टम / 'डिजिटल अरेस्ट' ची धमकी",
        "category:investment": "हमखास किंवा खूप जास्त परताव्याचे आश्वासन",
        "category:task_job": "सोप्या पार्ट-टाइम / टास्कमधून कमाईची ऑफर",
        "category:lottery_prize": "लॉटरी, बक्षीस किंवा भेट जिंकल्याचा दावा",
        "category:fake_customer_care": "बनावट कस्टमर केअर / रिफंड / रिमोट-ॲक्सेस ॲपचा प्रकार",
        "category:loan_app": "झटपट कर्ज ऑफरचा प्रकार",
        "category:utility_disconnection": "वीज / सेवा कनेक्शन तोडण्याची धमकी",
        "category:courier_parcel": "कुरिअर / पार्सल डिलिव्हरी समस्येचा प्रकार",
        "signal:threat_feed_url": "ही लिंक सार्वजनिक फिशिंग/मालवेअर यादीत आहे",
        "signal:lookalike_domain": "वेबसाइट अधिकृत ब्रँड डोमेनची नक्कल करते",
        "signal:brand_unofficial_contact": "{brand} असल्याचा दावा, पण लिंक किंवा क्रमांक अधिकृत नाही",
        "signal:shortener": "लहान लिंक खरी वेबसाइट लपवते",
        "signal:new_domain": "ही वेबसाइट आमच्या स्रोतांमध्ये आधी दिसली नाही",
        "signal:apk_link": "ॲप स्टोअरबाहेरून ॲप फाइल (.apk) इन्स्टॉल करायला सांगतो",
        "signal:urgency": "लगेच कृती करण्याचा दबाव",
        "signal:upi_payment_request": "UPI ID वर पैसे पाठवायला सांगतो",
        "signal:safe_browsing_hit": "Google Safe Browsing ही लिंक असुरक्षित असल्याचे सांगते",
        "signal:link_request": "स्कॅमसारख्या संदेशात लिंक उघडायला सांगतो",
        "signal:contact_mobile": "खाजगी मोबाइल क्रमांकावर कॉल किंवा मेसेज करायला सांगतो",
        "signal:group_invite": "खाजगी Telegram/WhatsApp 'टिप्स' ग्रुप जॉईन करण्याचा दबाव",
        "signal:sebi_claim_unverified": "SEBI नोंदणीचा दावा, जो आमच्या नोंदणी यादीत सापडला नाही",
        "factcheck": "फॅक्ट-चेक: {title} ({publisher}, {date})",
        "seen": "पहिल्यांदा दिसले {date}: {context}",
    },
}

SCAM_ADVICE = {
    "en": "🚨 Do not click links, share OTPs or pay anyone. Report fraud: call 1930 or visit https://cybercrime.gov.in",
    "hi": "🚨 किसी लिंक पर क्लिक न करें, OTP साझा न करें, पैसे न भेजें। धोखाधड़ी की शिकायत: 1930 पर कॉल करें या https://cybercrime.gov.in पर जाएं",
    "mr": "🚨 कोणत्याही लिंकवर क्लिक करू नका, OTP शेअर करू नका, पैसे पाठवू नका. फसवणुकीची तक्रार: 1930 वर कॉल करा किंवा https://cybercrime.gov.in ला भेट द्या",
}

DISCLAIMER = {
    "en": "ℹ️ Automated check by a prototype system. It can be wrong and does not confirm that anything is true. "
          "Verify with official sources.",
    "hi": "ℹ️ यह एक प्रोटोटाइप प्रणाली की स्वचालित जांच है। यह गलत हो सकती है और किसी बात को सच प्रमाणित नहीं करती। "
          "आधिकारिक स्रोतों से पुष्टि करें।",
    "mr": "ℹ️ ही एका प्रोटोटाइप प्रणालीची स्वयंचलित तपासणी आहे. ती चुकीची असू शकते आणि काहीही खरे असल्याचे सिद्ध करत नाही. "
          "अधिकृत स्रोतांकडून खात्री करा.",
}

CHECKING = {"en": "🔄 Checking… this can take a few seconds.", "hi": "🔄 जांच हो रही है… कुछ सेकंड लग सकते हैं।",
            "mr": "🔄 तपासणी सुरू आहे… काही सेकंद लागू शकतात."}

START = {
    "en": ("Welcome to the SOCMINT-India tipline (prototype).\n\nForward a message, link, photo, voice note or video and "
           "we will check it against fact-checks and scam patterns.\n\n🔒 What we store: a pseudonymous code instead of your "
           "Telegram ID (never your phone number, username or name); the message text with phone numbers, e-mails, "
           "Aadhaar/PAN-like numbers and UPI IDs removed; suspected scam links/numbers (not linked to you). Photos, "
           "voice notes and videos are deleted after {days} days.\n\nCommands: /help  /lang  /delete (erase everything you sent)."),
    "hi": ("SOCMINT-India टिपलाइन (प्रोटोटाइप) में आपका स्वागत है।\n\nकोई संदेश, लिंक, फ़ोटो, वॉयस नोट या वीडियो भेजें, हम उसे "
           "फ़ैक्ट-चेक और स्कैम पैटर्न से मिलाएंगे।\n\n🔒 हम क्या रखते हैं: आपकी Telegram ID की जगह एक छद्म कोड (आपका फ़ोन नंबर, "
           "यूज़रनेम या नाम कभी नहीं); संदेश का टेक्स्ट जिसमें से फ़ोन नंबर, ई-मेल, आधार/पैन जैसे नंबर और UPI ID हटा दिए जाते हैं; "
           "संदिग्ध स्कैम लिंक/नंबर (आपसे जुड़े नहीं)। फ़ोटो, वॉयस नोट और वीडियो {days} दिन बाद मिटा दिए जाते हैं।\n\n"
           "कमांड: /help  /lang  /delete (आपका भेजा सब कुछ मिटाएं)।"),
    "mr": ("SOCMINT-India टिपलाइन (प्रोटोटाइप) मध्ये आपले स्वागत आहे.\n\nएखादा संदेश, लिंक, फोटो, व्हॉइस नोट किंवा व्हिडिओ पाठवा, "
           "आम्ही तो फॅक्ट-चेक आणि स्कॅम पॅटर्नशी तपासू.\n\n🔒 आम्ही काय साठवतो: तुमच्या Telegram ID ऐवजी एक छद्म कोड (तुमचा फोन "
           "नंबर, युजरनेम किंवा नाव कधीच नाही); संदेशाचा मजकूर, ज्यातून फोन नंबर, ई-मेल, आधार/पॅनसारखे क्रमांक आणि UPI ID काढले "
           "जातात; संशयित स्कॅम लिंक/क्रमांक (तुमच्याशी जोडलेले नाहीत). फोटो, व्हॉइस नोट आणि व्हिडिओ {days} दिवसांनी हटवले जातात.\n\n"
           "कमांड: /help  /lang  /delete (तुम्ही पाठवलेले सर्व हटवा)."),
}

HELP = {
    "en": "Send any suspicious message, link, image, voice note or video. Replies: matches a fact-check • seen before • "
          "likely scam / suspicious • not verified yet (queued). /lang en|hi|mr changes language. /delete erases your data.",
    "hi": "कोई भी संदिग्ध संदेश, लिंक, फ़ोटो, वॉयस नोट या वीडियो भेजें। जवाब: फ़ैक्ट-चेक से मेल • पहले देखा गया • संभावित स्कैम / "
          "संदिग्ध • अभी पुष्टि नहीं (कतार में)। /lang en|hi|mr से भाषा बदलें। /delete से आपका डेटा मिटेगा।",
    "mr": "कोणताही संशयास्पद संदेश, लिंक, फोटो, व्हॉइस नोट किंवा व्हिडिओ पाठवा. उत्तरे: फॅक्ट-चेकशी जुळते • आधी दिसले • संभाव्य "
          "स्कॅम / संशयास्पद • अद्याप पडताळणी नाही (रांगेत). /lang en|hi|mr ने भाषा बदला. /delete ने तुमचा डेटा हटवा.",
}

LANG_SET = {"en": "Language set to English.", "hi": "भाषा हिंदी पर सेट की गई।", "mr": "भाषा मराठी वर सेट केली."}
DELETED = {"en": "Deleted {n} submission(s) linked to your pseudonymous code.",
           "hi": "आपके छद्म कोड से जुड़े {n} सबमिशन मिटा दिए गए।",
           "mr": "तुमच्या छद्म कोडशी जोडलेले {n} सबमिशन हटवले."}


def _lang(lang: str) -> str:
    return lang if lang in VERDICT_LINE else "en"


def reason_line(code: str, lang: str, fallback: str, **kw: str) -> str:
    tmpl = REASON_TEXT[_lang(lang)].get(code) or REASON_TEXT["en"].get(code) or fallback
    try:
        return tmpl.format(**kw)
    except (KeyError, IndexError):
        return tmpl


def disclaimer(lang: str) -> str:
    return DISCLAIMER[_lang(lang)]


def render_reply(result: CheckResult, lang: str) -> str:
    """Verdict line, 1-3 reasons, link if any, scam advice for scams, disclaimer."""
    lang = _lang(lang)
    lines = [VERDICT_LINE[lang][result.verdict]]
    bullets: list[str] = []
    link: str | None = None
    if result.verdict == Verdict.FACTCHECK_MATCH and result.matches:
        m = result.matches[0]
        date = m.published_at.strftime("%Y-%m-%d") if m.published_at else "n/a"
        bullets.append(reason_line("factcheck", lang, "", title=m.title[:140], publisher=m.publisher, date=date))
        link = m.url
    elif result.verdict == Verdict.SEEN_BEFORE and result.seen_before:
        sb = result.seen_before[0]
        bullets.append(reason_line("seen", lang, "", date=sb.first_seen.strftime("%Y-%m-%d"), context=sb.context[:140]))
        link = sb.url
    for r in result.reasons:
        if r.code.startswith(("category:", "signal:")) and len(bullets) < 3:
            brand = ""
            if r.code == "signal:brand_unofficial_contact":
                fs = r.evidence.get("findings") or [{}]
                brand = str(fs[0].get("brand_name") or fs[0].get("brand", ""))
            bullets.append(reason_line(r.code, lang, r.message, brand=brand))
    lines += [f"• {b}" for b in bullets[:3]]
    if link:
        lines.append(f"🔗 {link}")
    if result.verdict in (Verdict.LIKELY_SCAM, Verdict.SUSPICIOUS):
        lines.append(SCAM_ADVICE[lang])
    lines.append(disclaimer(lang))
    return "\n".join(lines)
