"""Deterministic pre-TTS text normalization for Hindi / English / Hinglish.

Applied ONLY to text on its way to the TTS provider (see
``elevenlabs_client.ElevenLabsTTSSession``). It never touches persisted
transcripts, tool-call arguments, DB values or anything structured: those keep
the model's original text. It is OFF unless the org's voice profile sets
``normalize_speech``; when off, ``TTSNormalizer.feed`` returns its input
unchanged and buffers nothing (zero added latency).

What it rewrites - only well-defined numeric forms:

* rupee amounts (``Rs. 1500``, ``₹1,50,000``, ``₹1.5 lakh``, paise),
* plain numbers (Indian lakh/crore grouping), decimals, ``%``,
* phone numbers and OTP/PIN codes, read digit by digit,
* dates (``25/03/2026``, ``2026-03-25``, ``25th March 2026``),
* clock times (``2:30 PM``, ``5 pm``, ``14:00``),
* a few unambiguous abbreviations (``GST``, ``EMI``, ``OTP``, ``Pvt. Ltd.``...),
* the org's own pronunciation dictionary (exact whole-word replacements).

Everything else - names, addresses, product words, punctuation (including the
Devanagari danda and question marks) - is left exactly as written. Words are
chosen per chunk from its script: Devanagari -> Hindi words in Devanagari,
romanized Hindi -> Hinglish words, otherwise English (Indian lakh/crore style).

Streaming: text arrives as small deltas, so ``TTSNormalizer`` buffers until a
clause boundary (``. , ; : ! ? ।`` followed by whitespace, not an abbreviation
dot) or ``MAX_HOLD`` characters, never cutting next to a number-ish token, and
``flush()`` releases the rest at the end of the turn.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

MAX_HOLD = 40  # chars held with no clause boundary before a safe cut is forced
_HARD_HOLD = 240  # absolute cap: emit everything rather than hold longer

# ---------------------------------------------------------------------------
# Number words
# ---------------------------------------------------------------------------

_EN_ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen"
).split()
_EN_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
_EN_ORD = (
    "_ first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth "
    "thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth "
    "twenty-first twenty-second twenty-third twenty-fourth twenty-fifth twenty-sixth "
    "twenty-seventh twenty-eighth twenty-ninth thirtieth thirty-first"
).split()

_HI_DEV = (
    "शून्य एक दो तीन चार पाँच छह सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह उन्नीस "
    "बीस इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस "
    "तीस इकतीस बत्तीस तैंतीस चौंतीस पैंतीस छत्तीस सैंतीस अड़तीस उनतालीस "
    "चालीस इकतालीस बयालीस तैंतालीस चौवालीस पैंतालीस छियालीस सैंतालीस अड़तालीस उनचास "
    "पचास इक्यावन बावन तिरपन चौवन पचपन छप्पन सत्तावन अट्ठावन उनसठ "
    "साठ इकसठ बासठ तिरसठ चौंसठ पैंसठ छियासठ सड़सठ अड़सठ उनहत्तर "
    "सत्तर इकहत्तर बहत्तर तिहत्तर चौहत्तर पचहत्तर छिहत्तर सतहत्तर अठहत्तर उनासी "
    "अस्सी इक्यासी बयासी तिरासी चौरासी पचासी छियासी सत्तासी अट्ठासी नवासी "
    "नब्बे इक्यानवे बानवे तिरानवे चौरानवे पचानवे छियानवे सत्तानवे अट्ठानवे निन्यानवे"
).split()
_HI_ROM = (
    "shunya ek do teen chaar paanch chhah saat aath nau das gyaarah baarah terah chaudah "
    "pandrah solah satrah atharah unnees "
    "bees ikkees baaees teyees chaubees pachchees chhabbees sattaaees atthaaees untees "
    "tees ikatees battees taintees chauntees paintees chhattees saintees adtees untaalees "
    "chaalees iktaalees bayaalees taintaalees chauvaalees paintaalees chhiyaalees saintaalees "
    "adtaalees unchaas "
    "pachaas ikyaavan baavan tirpan chauvan pachpan chhappan sattaavan atthaavan unsath "
    "saath iksath baasath tirsath chaunsath painsath chhiyaasath sadsath adsath unhattar "
    "sattar ikahattar bahattar tihattar chauhattar pachhattar chhihattar sathattar athahattar unaasi "
    "assi ikyaasi bayaasi tiraasi chauraasi pachaasi chhiyaasi sattaasi atthaasi navaasi "
    "nabbe ikyaanave baanave tiraanave chauraanave pachaanave chhiyaanave sattaanave "
    "atthaanave ninyaanave"
).split()
assert len(_HI_DEV) == 100 and len(_HI_ROM) == 100, (len(_HI_DEV), len(_HI_ROM))

_TABLES = {"hi": _HI_DEV, "hinglish": _HI_ROM}
_MAGNITUDES = {
    "hi": ("सौ", "हज़ार", "लाख", "करोड़"),
    "hinglish": ("sau", "hazaar", "lakh", "crore"),
}
_DIGITS = {
    "en": _EN_ONES[:10],
    "hi": _HI_DEV[:10],
    "hinglish": _HI_ROM[:10],
}

_WORDS = {
    "en": {
        "rupees": "rupees", "paise": "paise", "percent": "percent", "point": "point",
        "plus": "plus", "o_clock": "o'clock", "oh": "oh", "and": "and",
    },
    "hinglish": {
        "rupees": "rupaye", "paise": "paise", "percent": "pratishat", "point": "point",
        "plus": "plus", "baje": "baje", "bajkar": "bajkar", "minute": "minute",
        "sava": "sava", "saadhe": "saadhe", "paune": "paune", "dedh": "dedh", "dhaai": "dhaai",
        "subah": "subah", "dopahar": "dopahar", "shaam": "shaam", "raat": "raat", "and": "aur",
    },
    "hi": {
        "rupees": "रुपये", "paise": "पैसे", "percent": "प्रतिशत", "point": "पॉइंट",
        "plus": "प्लस", "baje": "बजे", "bajkar": "बजकर", "minute": "मिनट",
        "sava": "सवा", "saadhe": "साढ़े", "paune": "पौने", "dedh": "डेढ़", "dhaai": "ढाई",
        "subah": "सुबह", "dopahar": "दोपहर", "shaam": "शाम", "raat": "रात", "and": "और",
    },
}
_MONTHS_EN = (
    "January February March April May June July August September October November December"
).split()
_MONTHS_HI = (
    "जनवरी फ़रवरी मार्च अप्रैल मई जून जुलाई अगस्त सितंबर अक्टूबर नवंबर दिसंबर"
).split()
_MONTH_BY_PREFIX = {m[:3]: i for i, m in enumerate(_MONTHS_EN)}


def _en_below_100(n: int) -> str:
    if n < 20:
        return _EN_ONES[n]
    tens, ones = divmod(n, 10)
    return _EN_TENS[tens] + (f" {_EN_ONES[ones]}" if ones else "")


def _en_cardinal(n: int) -> str:
    if n < 100:
        return _en_below_100(n)
    parts: list[str] = []
    for unit, name in ((10**7, "crore"), (10**5, "lakh"), (1000, "thousand")):
        q, n = divmod(n, unit)
        if q:
            parts.append(f"{_en_cardinal(q)} {name}")
    q, n = divmod(n, 100)
    if q:
        parts.append(f"{_EN_ONES[q]} hundred")
    if n:
        parts.append(_en_below_100(n))
    return " ".join(parts)


def _hi_cardinal(n: int, lang: str) -> str:
    table = _TABLES[lang]
    sau, hazaar, lakh, crore = _MAGNITUDES[lang]
    if n < 100:
        return table[n]
    parts: list[str] = []
    for unit, name in ((10**7, crore), (10**5, lakh), (1000, hazaar)):
        q, n = divmod(n, unit)
        if q:
            parts.append(f"{_hi_cardinal(q, lang)} {name}")
    q, n = divmod(n, 100)
    if q:
        parts.append(f"{table[q]} {sau}")
    if n:
        parts.append(table[n])
    return " ".join(parts)


def cardinal(n: int, lang: str) -> str:
    return _en_cardinal(n) if lang == "en" else _hi_cardinal(n, lang)


def digits_spoken(digits: str, lang: str) -> str:
    return " ".join(_DIGITS[lang][int(c)] for c in digits if c.isdigit())


def _year_words(year: int, lang: str) -> str:
    if lang != "en":
        return _hi_cardinal(year, lang)
    if 2000 <= year <= 2009:
        return "two thousand" + (f" {_EN_ONES[year % 100]}" if year % 100 else "")
    hi, lo = divmod(year, 100)
    if lo == 0:
        return f"{_en_below_100(hi)} hundred"
    return f"{_en_below_100(hi)} {'oh ' + _EN_ONES[lo] if lo < 10 else _en_below_100(lo)}"


def speak_number(token: str, lang: str, *, as_digits: bool = False) -> str:
    """A digit string with optional thousands commas / decimal part -> words."""
    clean = token.replace(",", "")
    whole, _, frac = clean.partition(".")
    if as_digits or len(whole) > 9 or (len(whole) > 1 and whole.startswith("0")):
        out = digits_spoken(whole, lang)
    else:
        out = cardinal(int(whole), lang)
    if frac:
        out += f" {_WORDS[lang]['point']} {digits_spoken(frac, lang)}"
    return out


# ---------------------------------------------------------------------------
# Language detection (per chunk)
# ---------------------------------------------------------------------------

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_HINGLISH_MARKERS = frozenset(
    "hai hain hoon hun ka ki ke ko mein aap aapka aapki aapke kya nahi nahin haan ji kal aaj "
    "baje rupaye rupay tak aur bilkul theek karna karein kijiye liye wala wali mera meri hum "
    "hamare batao bataiye dhanyavaad namaste abhi phir sab kuch bhi toh kaise kyun kab kitna "
    "kitne kitni chahiye sakta sakti sakte raha rahi rahe "
    # Common verb forms and words that are not English, so a short clause is not mistaken for English.
    "lagega lagegi hoga hogi hoge rahega rahegi milega milegi banega banegi karenge karunga karungi karte "
    "karti dijiye lijiye batayein bataunga bataungi thoda zyada sirf lekin magar kyunki isliye yahan wahan "
    "lagbhag paise rupaiye mahine saal hafte samajh samjhe pata matlab achha accha bahut bohot".split()
)
_WORD_RE = re.compile(r"[A-Za-z]+")


def detect_language(text: str, preferred: str | None = None) -> str:
    """'hi' (Devanagari), 'hinglish' (romanized Hindi) or 'en'."""
    if _DEVANAGARI_RE.search(text):
        return "hi"
    if preferred == "en":
        return "en"
    if preferred in ("hi", "hinglish"):
        return "hinglish"
    words = {w.lower() for w in _WORD_RE.findall(text)}
    return "hinglish" if words & _HINGLISH_MARKERS else "en"


# ---------------------------------------------------------------------------
# Rewrites
# ---------------------------------------------------------------------------

_NUM = r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_MAG = r"lakhs?|lacs?|crores?|thousand|हज़ार|लाख|करोड़"

_CURRENCY_RE = re.compile(
    rf"(?<![A-Za-z])(?:Rs\.?|INR|₹)\s?(?P<num>{_NUM})(?P<mag>\s?(?:{_MAG})\b)?(?:\s?/-)?"
)
_PERCENT_RE = re.compile(rf"(?<![\w.])(?P<num>{_NUM})\s?%")
_MONTH_NAMES = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
    r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
)
_TEXT_DATE_RE = re.compile(
    rf"(?<![\w.])(?P<d>\d{{1,2}})(?:st|nd|rd|th)?(?P<of>\s+of)?\s+(?P<mon>{_MONTH_NAMES})\b"
    r"(?:,?\s+(?P<y>\d{4})(?!\d))?"
)
_NUM_DATE_RE = re.compile(r"(?<![\d/.-])(?P<d>\d{1,2})/(?P<m>\d{1,2})/(?P<y>\d{4})(?![\d/])")
_DASH_DATE_RE = re.compile(r"(?<![\d/.-])(?P<d>\d{1,2})-(?P<m>\d{1,2})-(?P<y>\d{4})(?![\d-])")
_ISO_DATE_RE = re.compile(r"(?<![\d-])(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})(?![\d-])")
_TIME_RE = re.compile(
    r"(?<![\d:.,/-])(?P<h>\d{1,2})(?::(?P<m>\d{2}))?(?:\s?(?P<ap>[AaPp][Mm]))?(?![\w:])"
    r"(?P<bj>\s?(?:baje|bje|बजे)(?![\w]))?"
)
_CODE_RE = re.compile(
    r"(?P<kw>\b(?i:otp|pin|pincode|pin code|code)\b\s*(?:(?i:is|hai|:)\s*)?)(?P<num>\d{3,8})(?!\w)"
)
_PHONE_IN_RE = re.compile(
    r"(?<![\w.,+-])(?P<cc>\+91[\s-]?|91[\s-]?|0)?(?P<n>[6-9]\d{4}[\s-]?\d{5})(?![\d,]|\.\d)"
)
_PHONE_INTL_RE = re.compile(r"(?<![\w+])\+\d{1,3}[\s-]?\d[\d\s-]{6,14}\d(?![\d])")
_BHK_RE = re.compile(r"(?<![\w.])(?P<n>\d{1,2})\s?BHK\b")
_PLAIN_NUM_RE = re.compile(rf"(?P<hash>#)?(?<![\w.])(?P<num>{_NUM})(?![\w])")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>]+")

_ABBREVIATIONS = frozenset(
    "GST EMI OTP KYC UPI PAN CRM FAQ PDF SMS URL IFSC ATM RBI NEFT RTGS IMPS ROI KPI API ID HR CEO CFO".split()
)
_ABBR_RE = re.compile(r"(?<![\w])(?:" + "|".join(sorted(_ABBREVIATIONS)) + r")(?![\w])")
_PVT_LTD_RE = re.compile(r"\bPvt\.?\s?Ltd\b")
_SQFT_RE = re.compile(r"(?i:\bsq\.?\s?ft\b)")
_APPROX_RE = re.compile(r"\bapprox\.(?=\s*[\d₹])")
_AMP_RE = re.compile(r"(?<=\s)&(?=\s)")


def _month_name(index: int, lang: str) -> str:
    return _MONTHS_HI[index] if lang == "hi" else _MONTHS_EN[index]


def _valid_dmy(d: int, m: int, y: int | None = None) -> bool:
    return 1 <= d <= 31 and 1 <= m <= 12 and (y is None or 1000 <= y <= 9999)


def _date_words(d: int, m: int, y: int | None, lang: str) -> str:
    day = _EN_ORD[d].replace("-", " ") if lang == "en" else cardinal(d, lang)
    out = f"{day} {_month_name(m - 1, lang)}"
    if y is not None:
        out += f" {_year_words(y, lang)}"
    return out


def _time_words(h: int, m: int, ap: str | None, lang: str) -> str | None:
    ap = ap.lower() if ap else None
    if m > 59:
        return None
    if ap:
        if not 1 <= h <= 12:
            return None
    else:
        if h > 23:
            return None
        if h > 12:
            h, ap = h - 12, "pm"
        elif h == 0:
            h, ap = 12, "am"
    w = _WORDS[lang]
    if lang == "en":
        hour = _en_cardinal(h)
        if m == 0:
            core = hour if ap else f"{hour} {w['o_clock']}"
            if not ap:
                return core
        else:
            core = f"{hour} {w['oh'] + ' ' + _EN_ONES[m] if m < 10 else _en_below_100(m)}"
        return f"{core} {'a m' if ap == 'am' else 'p m'}" if ap else core
    table = _TABLES[lang]

    def hour_word(x: int) -> str:
        x = (x - 1) % 12 + 1
        return table[x]

    if m == 0:
        body = f"{hour_word(h)} {w['baje']}"
    elif m == 15:
        body = f"{w['sava']} {hour_word(h)} {w['baje']}"
    elif m == 30:
        if h == 1:
            body = f"{w['dedh']} {w['baje']}"
        elif h == 2:
            body = f"{w['dhaai']} {w['baje']}"
        else:
            body = f"{w['saadhe']} {hour_word(h)} {w['baje']}"
    elif m == 45:
        body = f"{w['paune']} {hour_word(h + 1)} {w['baje']}"
    else:
        body = f"{hour_word(h)} {w['bajkar']} {table[m]} {w['minute']}"
    if ap == "am":
        part = w["subah"] if 4 <= h <= 11 else w["raat"]
    elif ap == "pm":
        part = w["dopahar"] if (h == 12 or h <= 3) else (w["shaam"] if h <= 7 else w["raat"])
    else:
        part = ""
    return f"{part} {body}".strip()


def _phone_words(digits: str, lang: str, cc: str = "") -> str:
    groups = [digits[i : i + 5] for i in range(0, len(digits), 5)]
    spoken = ", ".join(digits_spoken(g, lang) for g in groups)
    cc_digits = re.sub(r"\D", "", cc)
    if cc.startswith("+") and cc_digits:
        return f"{_WORDS[lang]['plus']} {digits_spoken(cc_digits, lang)}, {spoken}"
    if cc_digits:
        return f"{digits_spoken(cc_digits, lang)}, {spoken}"
    return spoken


def normalize_text(
    text: str,
    *,
    language: str | None = None,
    pronunciations: Iterable[tuple[str, str]] = (),
) -> str:
    """Rewrite one chunk of speech text. Pure and deterministic."""
    if not text or any("\ue000" <= ch <= "\uf8ff" for ch in text):
        return text  # empty, or already contains our placeholder range: leave alone
    lang = detect_language(text, language)
    store: list[str] = []

    def hold(spoken: str) -> str:
        store.append(spoken)
        return chr(0xE000 + len(store) - 1)

    # Org dictionary first, protected from every later rule.
    for word, spoken in sorted(pronunciations, key=lambda kv: -len(kv[0])):
        pat = re.compile(r"(?<![\w\u0900-\u097F])" + re.escape(word) + r"(?![\w\u0900-\u097F])")
        text = pat.sub(lambda _m, s=spoken: hold(s), text)
    text = _URL_RE.sub(lambda m: hold(m.group(0)), text)
    text = _EMAIL_RE.sub(
        lambda m: hold(m.group(0).replace("@", " at ").replace(".", " dot ")), text
    )

    def currency(m: re.Match[str]) -> str:
        num = m.group("num").replace(",", "")
        whole, _, frac = num.partition(".")
        w = _WORDS[lang]
        if m.group("mag") or (frac and len(frac) != 2):
            body = speak_number(num, lang)
            mag = (m.group("mag") or "").strip()
            return f"{body}{' ' + mag if mag else ''} {w['rupees']}"
        body = f"{speak_number(whole, lang)} {w['rupees']}"
        if frac and int(frac):
            body += f" {speak_number(frac, lang)} {w['paise']}"
        return body

    text = _CURRENCY_RE.sub(currency, text)
    text = _PERCENT_RE.sub(lambda m: f"{speak_number(m.group('num'), lang)} {_WORDS[lang]['percent']}", text)

    def text_date(m: re.Match[str]) -> str:
        d = int(m.group("d"))
        mon = _MONTH_BY_PREFIX[m.group("mon")[:3]] + 1
        y = int(m.group("y")) if m.group("y") else None
        if not _valid_dmy(d, mon, y):
            return m.group(0)
        out = _date_words(d, mon, y, lang)
        if m.group("of"):
            day, _, rest = out.partition(" ")
            out = f"{day} of {rest}" if lang == "en" else out
        return out

    text = _TEXT_DATE_RE.sub(text_date, text)

    def numeric_date(order: str):
        def repl(m: re.Match[str]) -> str:
            d, mon, y = int(m.group("d")), int(m.group("m")), int(m.group("y"))
            return _date_words(d, mon, y, lang) if _valid_dmy(d, mon, y) else m.group(0)

        return repl

    text = _NUM_DATE_RE.sub(numeric_date("dmy"), text)
    text = _DASH_DATE_RE.sub(numeric_date("dmy"), text)
    text = _ISO_DATE_RE.sub(numeric_date("ymd"), text)

    def time_repl(m: re.Match[str]) -> str:
        if m.group("m") is None and not m.group("ap"):
            return m.group(0)  # a bare number is not a time
        out = _time_words(int(m.group("h")), int(m.group("m") or 0), m.group("ap"), lang)
        if out is None:
            return m.group(0)
        # The spoken Hindi/Hinglish form already says "baje": do not say it twice ("baarah baje baje").
        return out + (m.group("bj") or "") if lang == "en" else out

    text = _TIME_RE.sub(time_repl, text)
    text = _CODE_RE.sub(lambda m: m.group("kw") + digits_spoken(m.group("num"), lang), text)
    text = _PHONE_IN_RE.sub(
        lambda m: _phone_words(re.sub(r"\D", "", m.group("n")), lang, m.group("cc") or ""), text
    )
    text = _PHONE_INTL_RE.sub(
        lambda m: _phone_words(re.sub(r"\D", "", m.group(0)), lang, "+") if len(re.sub(r"\D", "", m.group(0))) >= 9 else m.group(0),
        text,
    )
    text = _BHK_RE.sub(lambda m: f"{speak_number(m.group('n'), lang)} B H K", text)
    text = _PLAIN_NUM_RE.sub(
        lambda m: speak_number(m.group("num"), lang, as_digits=bool(m.group("hash")))
        if not m.group("hash")
        else "# " + speak_number(m.group("num"), lang, as_digits=True),
        text,
    )
    text = _PVT_LTD_RE.sub("Private Limited", text)
    text = _SQFT_RE.sub("square feet" if lang != "hi" else "स्क्वायर फीट", text)
    text = _APPROX_RE.sub("approximately", text)
    text = _AMP_RE.sub(_WORDS[lang]["and"], text)
    text = _ABBR_RE.sub(lambda m: " ".join(m.group(0)), text)

    if store:
        text = re.sub("[\ue000-\uf8ff]", lambda m: store[ord(m.group(0)) - 0xE000], text)
    return text


# ---------------------------------------------------------------------------
# Streaming wrapper
# ---------------------------------------------------------------------------

_BOUNDARY_CHARS = ".,;:!?\n।"
_ABBREV_TOKENS = frozenset(
    "rs no mr mrs ms dr st approx pvt ltd vs sq ft inr cr e.g i.e etc".split()
)
_NUMBERISH_TOKENS = frozenset("rs rs. inr ₹ otp pin code pincode sq pvt approx approx. no no. is".split())
_NUMBERISH_RE = re.compile(r"\d|[₹+#%/:-]$|^\W*$")


def _is_abbrev_dot(buf: str, i: int) -> bool:
    if buf[i] != ".":
        return False
    m = re.search(r"(\S+)$", buf[:i])
    if m is None:
        return False
    token = m.group(1).lower()
    return token in _ABBREV_TOKENS or bool(re.fullmatch(r"(?:\w\.)*\w", token))


class TTSNormalizer:
    """Streaming wrapper around ``normalize_text``. ``feed(delta)`` returns the
    text that is safe to send now ('' while buffering); ``flush()`` returns the
    remainder at the end of the turn. When ``enabled`` is False it is the
    identity function with no buffering."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        language: str | None = None,
        pronunciations: Iterable[tuple[str, str]] = (),
    ) -> None:
        self.enabled = enabled
        self._language = language
        self._pron = tuple(pronunciations)
        self._buf = ""
        # Chunks are converted one at a time, so a Hinglish turn could flip to English number words on a clause
        # that happens to carry no Hindi marker ("... hai, Rs 5,000" -> "five thousand rupees"). Once any chunk of
        # this turn is Hindi/Hinglish, later chunks without a language of their own follow it.
        self._turn_language: str | None = None

    def _convert(self, chunk: str) -> str:
        language = self._language
        if language is None:
            detected = detect_language(chunk)
            if detected != "en":
                self._turn_language = "hinglish"
            elif self._turn_language:
                language = self._turn_language
        return normalize_text(chunk, language=language, pronunciations=self._pron)

    @staticmethod
    def _boundary(buf: str) -> int:
        """Index just past the last clause boundary (plus its whitespace), or 0."""
        for i in range(len(buf) - 1, -1, -1):
            ch = buf[i]
            if ch not in _BOUNDARY_CHARS:
                continue
            if ch == "।" or ch == "\n":
                return i + 1
            if i + 1 < len(buf) and buf[i + 1].isspace() and not _is_abbrev_dot(buf, i):
                return i + 2
        return 0

    @staticmethod
    def _safe_space_cut(buf: str) -> int:
        """Last whitespace we may cut at without splitting a number-ish token
        from its neighbour; 0 if none."""
        for p in range(len(buf) - 1, 0, -1):
            if not buf[p].isspace():
                continue
            left = buf[:p].split()[-1] if buf[:p].split() else ""
            if _NUMBERISH_RE.search(left) or left.lower() in _NUMBERISH_TOKENS:
                continue
            return p + 1
        return 0

    def feed(self, delta: str) -> str:
        if not self.enabled:
            return delta
        self._buf += delta
        cut = self._boundary(self._buf)
        if not cut and len(self._buf) >= MAX_HOLD:
            cut = self._safe_space_cut(self._buf)
        if not cut and len(self._buf) >= _HARD_HOLD:
            cut = len(self._buf)
        if not cut:
            return ""
        head, self._buf = self._buf[:cut], self._buf[cut:]
        return self._convert(head)

    def flush(self) -> str:
        if not self.enabled:
            return ""
        tail, self._buf = self._buf, ""
        return self._convert(tail) if tail else ""
