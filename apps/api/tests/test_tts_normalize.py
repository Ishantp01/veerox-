"""Pre-TTS normalization: Hindi / Hinglish / English representative cases,
streaming behaviour, dictionary, untouched names, disabled == identity."""

from __future__ import annotations

import json

import pytest

from apps.api.channels.voice import elevenlabs_client
from apps.api.channels.voice.tts_normalize import (
    TTSNormalizer,
    cardinal,
    detect_language,
    normalize_text,
)
from apps.api.channels.voice.voice_profile import resolve_profile


def n(text: str, **kw) -> str:
    return normalize_text(text, **kw)


# --- number words ---------------------------------------------------------------------------------


def test_cardinals_english_indian_grouping() -> None:
    assert cardinal(1500, "en") == "one thousand five hundred"
    assert cardinal(150000, "en") == "one lakh fifty thousand"
    assert cardinal(25000000, "en") == "two crore fifty lakh"
    assert cardinal(0, "en") == "zero"


def test_cardinals_hindi_and_hinglish() -> None:
    assert cardinal(1500, "hinglish") == "ek hazaar paanch sau"
    assert cardinal(150000, "hinglish") == "ek lakh pachaas hazaar"
    assert cardinal(99, "hi") == "निन्यानवे"
    assert cardinal(2026, "hi") == "दो हज़ार छब्बीस"


# --- amounts --------------------------------------------------------------------------------------


def test_rupee_amounts_each_language() -> None:
    assert n("Your bill is Rs. 1500 only.") == "Your bill is one thousand five hundred rupees only."
    assert n("Total ₹1,50,000 hai") == "Total ek lakh pachaas hazaar rupaye hai"
    assert n("आपका बिल ₹2,500 है।") == "आपका बिल दो हज़ार पाँच सौ रुपये है।"
    assert n("Fee INR 99.50") == "Fee ninety nine rupees fifty paise"


def test_lakh_style_amount_keeps_the_magnitude_word() -> None:
    assert n("It costs ₹1.5 lakh") == "It costs one point five lakh rupees"
    assert n("1500 rupees only") == "one thousand five hundred rupees only"


def test_percent() -> None:
    assert n("15% discount") == "fifteen percent discount"
    assert n("Discount 15% hai") == "Discount pandrah pratishat hai"


# --- phone numbers / codes ------------------------------------------------------------------------


def test_phone_numbers_digit_by_digit() -> None:
    assert n("Call 9876543210 now") == "Call nine eight seven six five, four three two one zero now"
    assert n("+91 98765 43210") == "plus nine one, nine eight seven six five, four three two one zero"
    assert n("नंबर 9876543210 है") == "नंबर नौ आठ सात छह पाँच, चार तीन दो एक शून्य है"


def test_otp_and_order_ids_are_read_as_digits() -> None:
    assert n("Your OTP is 482913, thanks") == "Your O T P is four eight two nine one three, thanks"
    assert n("Order #4521 aaya") == "Order # four five two one aaya"


# --- dates and times ------------------------------------------------------------------------------


def test_numeric_and_textual_dates() -> None:
    assert n("on 25/03/2026") == "on twenty fifth March twenty twenty six"
    assert n("25/03/2026 ko aana") == "pachchees March do hazaar chhabbees ko aana"
    assert n("2026-03-25") == "twenty fifth March twenty twenty six"
    assert n("Meet on 25th March 2026.") == "Meet on twenty fifth March twenty twenty six."
    assert n("5 April ko") == "paanch April ko"


def test_impossible_dates_are_not_turned_into_dates() -> None:
    # No month word is invented: the parts are just read as plain numbers.
    assert n("31/13/2026") == "thirty one/thirteen/two thousand twenty six"


def test_times_english_hinglish_hindi() -> None:
    assert n("at 2:30 PM sharp") == "at two thirty p m sharp"
    assert n("at 5 pm.") == "at five p m."
    assert n("Is it 3:15 PM?") == "Is it three fifteen p m?"
    assert n("Meeting 2:30 PM par hai") == "Meeting dopahar dhaai baje par hai"
    assert n("आपकी मीटिंग 2:30 PM पर है") == "आपकी मीटिंग दोपहर ढाई बजे पर है"
    assert n("kal 10:00 AM ko aana") == "kal subah das baje ko aana"
    assert n("kal 6:45 PM par aana hai") == "kal shaam paune saat baje par aana hai"


def test_bare_number_is_not_a_time() -> None:
    assert n("5 baje aana") == "paanch baje aana"


def test_invalid_times_are_not_read_as_times() -> None:
    assert n("13 pm") == "thirteen pm"


# --- abbreviations --------------------------------------------------------------------------------


def test_business_abbreviations() -> None:
    assert n("GST and EMI details") == "G S T and E M I details"
    assert n("Acme Pvt. Ltd. ka office") == "Acme Private Limited. ka office"
    assert n("2BHK, 1200 sq ft") == "two B H K, one thousand two hundred square feet"
    assert n("Tom & Jerry") == "Tom and Jerry"
    assert n("Tom & Jerry hai") == "Tom aur Jerry hai"


def test_email_and_url_handling() -> None:
    assert n("mail a.b@x.com") == "mail a dot b at x dot com"
    assert n("see https://x.com/a1 now") == "see https://x.com/a1 now"


# --- must NOT change ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Hello Rohit",
        "Namaste Rajeshwari ji, main Venkatesh Subramaniam ke office se bol raha hoon.",
        "Kya main Abhishek Chaturvedi se baat kar sakta hoon?",
        "क्या आप कल फ्री हैं?",
        "आपका स्वागत है।",
        "Kya aap free hain?",
        "Sector A101 mein",
        "Version v2beta",
    ],
)
def test_names_questions_and_punctuation_untouched(text: str) -> None:
    assert n(text) == text


def test_question_mark_and_danda_preserved_around_rewrites() -> None:
    assert n("Kya 5 pm theek hai?") == "Kya shaam paanch baje theek hai?"
    assert n("बिल ₹500 है।") == "बिल पाँच सौ रुपये है।"


# --- language detection ---------------------------------------------------------------------------


def test_language_detection() -> None:
    assert detect_language("आपका बिल") == "hi"
    assert detect_language("aapka bill hai") == "hinglish"
    assert detect_language("Your bill is due") == "en"
    assert detect_language("Your bill", preferred="hi") == "hinglish"
    assert detect_language("aapka bill hai", preferred="en") == "en"
    assert detect_language("आपका", preferred="en") == "hi"  # script wins


# --- dictionary -----------------------------------------------------------------------------------


def test_org_dictionary_exact_whole_word_only() -> None:
    pron = [("Veerox", "Veer ox"), ("Sachdeva", "Sachdev")]
    assert n("Veerox se Sachdeva ji", pronunciations=pron) == "Veer ox se Sachdev ji"
    # not inside a longer word, case-sensitive
    assert n("Veeroxes veerox", pronunciations=pron) == "Veeroxes veerox"


def test_dictionary_output_is_not_rewritten_by_numeric_rules() -> None:
    assert n("Room 101 hai", pronunciations=[("Room 101", "Room 1 0 1")]) == "Room 1 0 1 hai"
    assert n("Pay GST", pronunciations=[("GST", "gee es tee")]) == "Pay gee es tee"


def test_no_dictionary_means_names_never_changed() -> None:
    assert n("Veerox hai") == "Veerox hai"


# --- streaming ------------------------------------------------------------------------------------


def _stream(deltas: list[str], **kw) -> str:
    norm = TTSNormalizer(enabled=True, **kw)
    out = [norm.feed(d) for d in deltas]
    out.append(norm.flush())
    return "".join(out)


def test_disabled_is_identity_and_does_not_buffer() -> None:
    norm = TTSNormalizer(enabled=False)
    assert norm.feed("Rs. 15") == "Rs. 15"
    assert norm.feed("00 only") == "00 only"
    assert norm.flush() == ""


def test_number_split_across_deltas_is_converted_whole() -> None:
    text = "Total Rs. 1500 only. Time 2:30 PM, ok? Call 9876543210 now."
    for size in (1, 2, 3, 5, 7):
        deltas = [text[i : i + size] for i in range(0, len(text), size)]
        assert _stream(deltas) == n(text), size


def test_streaming_matches_one_shot_for_hindi_and_hinglish() -> None:
    for text in (
        "आपका बिल ₹2,500 है। मीटिंग 2:30 PM पर है।",
        "Aapka bill Rs. 1500 hai, aur 25/03/2026 ko 5 pm par aana hai.",
    ):
        deltas = [text[i : i + 3] for i in range(0, len(text), 3)]
        assert _stream(deltas) == n(text)


def test_stream_holds_text_until_a_clause_boundary_then_flush_releases_the_rest() -> None:
    norm = TTSNormalizer(enabled=True)
    assert norm.feed("Total is Rs") == ""
    assert norm.feed(". 1500") == ""  # 'Rs.' is an abbreviation dot, not a boundary
    out = norm.feed(" only. Bye")
    assert out == "Total is one thousand five hundred rupees only. "
    assert norm.flush() == "Bye"


def test_long_clause_without_punctuation_is_released_without_splitting_a_number() -> None:
    text = "please note that the final amount payable by you will be Rs. 1500 after taxes and fees"
    deltas = [text[i : i + 4] for i in range(0, len(text), 4)]
    out = _stream(deltas)
    assert "one thousand five hundred rupees" in out
    assert out == n(text)


# --- wired into the ElevenLabs session -------------------------------------------------------------


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def close(self) -> None: ...

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


async def _session_texts(monkeypatch: pytest.MonkeyPatch, profile) -> list[dict]:
    ws = _FakeWS()

    async def fake_connect(url: str):
        return ws

    monkeypatch.setattr(elevenlabs_client.websockets, "connect", fake_connect)
    monkeypatch.setattr(elevenlabs_client.settings, "elevenlabs_api_key", "sk_test")
    async with elevenlabs_client.ElevenLabsTTSSession(profile=profile) as session:
        for piece in ("Pay Rs", ". 15", "00 by 5 ", "pm today"):
            await session.send_text(piece)
        await session.finish()
    return ws.sent[1:]  # drop the handshake


async def test_session_normalizes_only_when_profile_enables_it(monkeypatch: pytest.MonkeyPatch) -> None:
    off = await _session_texts(monkeypatch, None)
    assert [m["text"] for m in off] == ["Pay Rs", ". 15", "00 by 5 ", "pm today", ""]

    on = await _session_texts(monkeypatch, resolve_profile({"normalize_speech": True}))
    spoken = "".join(m["text"] for m in on)
    assert spoken == "Pay one thousand five hundred rupees by five p m today"
    assert on[-1] == {"text": ""}  # the flush marker is still last
