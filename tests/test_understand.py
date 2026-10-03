"""M2: language ID, normalisation and entity extractors on mixed-script text."""

from __future__ import annotations

import pytest

from socmint.understand.entities import (
    canonical_phone,
    extract_all,
    extract_brands,
    extract_phones,
    extract_tickers,
    extract_upi,
    extract_urls,
)
from socmint.understand.lang import detect_lang, reply_language
from socmint.understand.normalize import match_text, strip_emoji

SYMS = frozenset({"DEMOPUMP", "DEMOSTEADY", "RELIANCE", "IT"})


@pytest.mark.parametrize("text,expected", [
    ("Call +91 98765 43210 now", ["+919876543210"]),
    ("call 9876543210 or 09876543211", ["+919876543210", "+919876543211"]),
    ("संपर्क करें ९८७६५४३२१० पर", ["+919876543210"]),
    ("तुमचा नंबर ९०००००० १०३ आहे", ["+919000000103"]),
    ("toll free 1800-11-2211 and 1800 425 3800", ["1800112211", "18004253800"]),
    ("office 022-23456789", ["02223456789"]),
    ("order id 1234567890123 amount 5000", []),
    ("aadhaar 2345 6789 0123", []),
])
def test_phones(text, expected):
    assert extract_phones(text) == expected


def test_canonical_phone():
    assert canonical_phone("+91-98765-43210") == "+919876543210"
    assert canonical_phone("1912") == "1912"


@pytest.mark.parametrize("text,expected", [
    ("pay to demo.safe@demoupi now", ["demo.safe@demoupi"]),
    ("UPI: 9876543210@ybl पर भेजें", ["9876543210@ybl"]),
    ("email me at raj@example.com", []),
    ("तुरंत prize.hi@demoupi पर ₹4999 भेजें", ["prize.hi@demoupi"]),
])
def test_upi(text, expected):
    assert extract_upi(text) == expected


@pytest.mark.parametrize("text,urls,hosts", [
    ("visit http://demobank-kyc.example/login now", ["http://demobank-kyc.example/login"], ["demobank-kyc.example"]),
    ("खाता अपडेट करें: https://sbi-kyc-update.in/x?a=1।", ["https://sbi-kyc-update.in/x?a=1"], ["sbi-kyc-update.in"]),
    ("go to sbi-kyc.in/verify today", ["http://sbi-kyc.in/verify"], ["sbi-kyc.in"]),
    ("www.Example.com/Path).", ["http://www.example.com/Path"], ["www.example.com"]),
    ("ok.how are you, file photo.zip, Rs.500, e.g. this", [], []),
    ("mail info@sbi.co.in or pay a@ybl", [], []),
    ("login at https://www.hdfc.bank.in/", ["https://www.hdfc.bank.in/"], ["www.hdfc.bank.in"]),
])
def test_urls(text, urls, hosts):
    u, h = extract_urls(text)
    assert u == urls and h == hosts


@pytest.mark.parametrize("text,expected", [
    ("Buy $DEMOPUMP now", ["DEMOPUMP"]),
    ("#DEMOSTEADY to the moon", ["DEMOSTEADY"]),
    ("DEMOPUMP शेयर जल्दी खरीदो", ["DEMOPUMP"]),
    ("DEMOPUMP शेअर लगेच खरेदी करा", ["DEMOPUMP"]),
    ("I met DEMOPUMP people yesterday", []),            # no stock context word
    ("IT stocks are falling", []),                      # 2-letter symbols need $/#
    ("$NOTASYMBOL target 500", []),                     # cashtag not in the symbol list
])
def test_tickers(text, expected):
    assert extract_tickers(text, SYMS) == expected


def test_brands_multiscript():
    assert extract_brands("Your SBI YONO account") == ["sbi"]
    assert extract_brands("आपका एसबीआई खाता") == ["sbi"]
    assert extract_brands("तुमचे डेमोबँक खाते") == ["demobank"]
    assert extract_brands("महावितरण वीज बिल") == ["msedcl"]
    assert extract_brands("bisbi tsbi") == []           # word boundaries for Latin aliases


def test_extract_all_on_mixed_message():
    e = extract_all("डेमोबैंक: KYC करें http://demobank-kyc.example/hi, कॉल 9000000102, UPI kyc.fee@demoupi", SYMS)
    assert e.brands == ["demobank"] and e.domains == ["demobank-kyc.example"]
    assert e.phones == ["+919000000102"] and e.upi_ids == ["kyc.fee@demoupi"]


@pytest.mark.parametrize("text,code", [
    ("Your account will be blocked today, update KYC immediately", "en"),
    ("आपका खाता आज ब्लॉक हो जाएगा, तुरंत केवाईसी अपडेट करें", "hi"),
    ("तुमचे खाते आज बंद होणार आहे, लगेच केवायसी अपडेट करा", "mr"),
    ("Aapka account aaj band ho jayega, turant KYC update karo", "hi-Latn"),
    ("झटपट कर्ज मंजूर! लोन ॲप डाउनलोड करा आजच", "mr"),
])
def test_lang(text, code):
    assert detect_lang(text).code == code


def test_reply_language_mapping():
    assert reply_language("hi-Latn") == "en" and reply_language("mr") == "mr" and reply_language("ta") == "en"


def test_normalisation():
    assert match_text("KYC ⚠️ Update!! ९८७६") == "kyc update!! 9876"
    assert match_text("फ़र्ज़ी") == match_text("फर्जी")
    assert strip_emoji("ok 👍🏽 done") == "ok  done"
