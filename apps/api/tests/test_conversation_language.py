"""Reply language follows what the customer just wrote (Phase 1: replies in
Hindi, English and Hinglish). Includes the spelling variants real customers
type, which the first version of the detector missed."""

from __future__ import annotations

import pytest

from apps.api.core.conversation.understand import detect_language

HINDI = [
    "नमस्ते आप कैसे हैं",
    "कीमत कितनी है",
    "मुझे अभी बात नहीं करनी",
    "कल शाम 5 बजे कॉल करना",
]

HINGLISH = [
    "iska price kya hai",
    "abhi busy hoon, kal call karna",
    "Kal shaam 5 bje call krna",
    "kal sham ko 6 baje call karo",
    "mujhe details bhej do",
    "mujhe nhi chahiye",
    "aapka plan kitna ka hai",
    "bahut mehenga hai bhai",
    "subah 10 bje baat karte hain",
    "haan batao kya offer hai",
    "kyaaa bol rahe ho aap",
    "haiii theek hai",
    "price kitna hoga",
    "dobara call karna shaam ko",
    "mujhe samajh nahi aaya",
    "aaj nahi, parso call kro",
    "demo kab milega",
    "main kal call krunga lekin abhi busy hu",
    "aur koi plan hai kya",
    "thik hai bhej dijiye",
]

ENGLISH = [
    "Do you integrate with Tally?",
    "What is the price of the premium plan",
    "I am busy right now, call me tomorrow at 5 pm",
    "please stop messaging me",
    "I want to talk to someone from your team",
    "hello",
    "Can you deliver to Pune warehouse by Friday",
    "I do not know",
    "this is useless",
    "how much does it cost",
    "send me the details on whatsapp",
    "we are planning something big",
]


@pytest.mark.parametrize("text", HINDI)
def test_devanagari_is_hindi(text: str) -> None:
    assert detect_language(text) == "hi"


@pytest.mark.parametrize("text", HINGLISH)
def test_latin_script_hindi_is_hinglish(text: str) -> None:
    assert detect_language(text) == "hinglish"


@pytest.mark.parametrize("text", ENGLISH)
def test_english_stays_english(text: str) -> None:
    assert detect_language(text) == "en"


def test_overall_accuracy_target() -> None:
    cases = (
        [(t, "hi") for t in HINDI]
        + [(t, "hinglish") for t in HINGLISH]
        + [(t, "en") for t in ENGLISH]
    )
    correct = sum(detect_language(text) == expected for text, expected in cases)
    assert correct / len(cases) >= 0.95
