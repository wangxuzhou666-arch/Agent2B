"""Whether a request negates the phrase it names (core/routing/negation.py): a negator of its
language before the phrase, read from grammar. A negated write never goes straight to its
approval (rule_arm), whether or not the request also reads as a question. 别 negates wherever it
is written, except inside the listed words in which it is no imperative "don't"."""

import pytest

from twotrack.core.routing import is_question, negates
from twotrack.core.routing.negation import BIE_WORDS, bie_negates
from twotrack.core.text import phrase_spans


def negated(text: str, phrase: str) -> bool:
    spans = phrase_spans(phrase, text)
    assert spans, (phrase, text)  # the phrase is there to negate
    return negates(text, spans)


@pytest.mark.parametrize("text, phrase", [
    # Chinese: every negator, and 无须, the other spelling of 无需
    ("别给 SKU-1 下单 40 件", "下单"),
    ("不要给 SKU-1 下单 40 件", "下单"),
    ("SKU-1 不用下单了", "下单"),
    ("无需下单", "下单"),
    ("无须下单", "下单"),
    ("这次不必下单", "下单"),
    ("请勿下单", "下单"),
    ("甭下单了", "下单"),
    ("禁止给 SKU-1 下单 40 件", "下单"),
    ("不准下单", "下单"),
    ("不许下单", "下单"),
    ("千万别再下单", "下单"),
    ("不需要下单", "下单"),
    ("这次不需下单", "下单"),
    # English: whole words, in any case, with either apostrophe or none
    ("don't place an order", "place an order"),
    ("DON’T place an order", "place an order"),
    ("dont place an order", "place an order"),
    ("please do not place an order", "place an order"),
    ("never place an order", "place an order"),
    ("no need to place an order", "place an order"),
    ("no need for a new one, place an order", "place an order"),
    # before any occurrence of the phrase
    ("下单 SKU-1 40 件，别下单 SKU-2", "下单"),
])
def test_a_negator_before_the_phrase_negates_it(text, phrase):
    assert negated(text, phrase)


@pytest.mark.parametrize("text, phrase", [
    ("don't forget to place an order", "place an order"),
    ("never mind the plan, place an order", "place an order"),
    ("不用担心预算，下单", "下单"),
    ("不需要的旧料先放着，下单", "下单"),
])
def test_it_errs_towards_a_negation(text, phrase):
    """Whatever the negator negates, in whichever clause: reading such a command as negated
    costs it one confirmation, the cheaper mistake."""
    assert negated(text, phrase)


@pytest.mark.parametrize("text", [
    "别下单",  # opening the request
    "SKU-1 别下单",  # after a digit, a space
    "你别下单", "请别下单", "千万别下单", "今天别下单",
    "这批货别下单",  # after a noun
    "记得别下单",  # after a verb
    "别特别急着下单",  # next to a listed word, but not inside it
    "别人说别下单",  # the second one
    "特别急，别下单",
])
def test_bie_negates_wherever_it_is_written(text):
    assert negated(text, "下单")


@pytest.mark.parametrize("word", sorted(BIE_WORDS))
def test_bie_inside_a_listed_word_negates_nothing(word):
    assert "别" in word and len(word) == 2
    assert not negated(f"{word}，给 SKU-1 下单", "下单")
    assert not negated(f"按{word}看，下单", "下单")


@pytest.mark.parametrize("text", [
    "别无选择，下单",  # "no other choice"
    "有别于上次，下单",  # "unlike last time"
    "分门别类整理好了，下单",  # "sorted by kind"
    "价格天壤之别，下单",  # "a world of difference"
    "别看量小，下单",  # "small as it is": 别 concedes, it does not forbid
])
def test_bie_outside_the_listed_words_is_a_known_false_negation(text):
    """Known false positives, kept on purpose: the list is closed, and a word or an idiom it does
    not hold reads as a negation, which costs one confirmation, never an approval card."""
    assert negated(text, "下单")


@pytest.mark.parametrize("text", [
    "交货不准时，下单",  # "not on time"
    "报价不准确，下单",  # "inaccurate"
])
def test_buzhun_inside_other_words_is_a_known_false_negation(text):
    """Known false positives, kept on purpose: 不准 has no list of the words it is part of (as 别
    has), so it negates there too, which costs one confirmation, never an approval card."""
    assert negated(text, "下单")


def test_bie_negates_reads_the_characters_on_both_sides():
    assert bie_negates("别", 0)
    assert not bie_negates("特别", 1) and not bie_negates("别的", 0)
    assert bie_negates("别下", 0) and bie_negates("货别", 1)
    assert not bie_negates("告别人", 1)  # inside both 告别 and 别人


@pytest.mark.parametrize("text, phrase", [
    ("给 SKU-1 下单 40 件", "下单"),
    ("place an order for 40 units", "place an order"),
    # after the phrase, a negator negates something else
    ("下单 40 件，不用加急", "下单"),
    ("下单 40 件，禁止加急", "下单"),
    ("place an order, no need to rush", "place an order"),
    ("place an order and don't wait", "place an order"),
    # whole words only in English
    ("nevertheless, place an order", "place an order"),
    ("do nothing else, place an order", "place an order"),
    # a plain 不 negates a statement as often as a command, so it is no negator
    ("库存不足，下单", "下单"),
    ("先不下单", "下单"),
    # 别 inside a listed word
    ("特别急，下单", "下单"),
    ("别的供应商也行，下单", "下单"),
])
def test_no_negator_before_the_phrase(text, phrase):
    assert not negated(text, phrase)


def test_nothing_matched_nothing_negated():
    assert not negates("don't", [])


def test_it_stands_without_the_question_rule():
    """'don't' that opens a clause reads as a question too; these do not, and are negated."""
    for text, phrase in (("please don't place an order", "place an order"),
                         ("I don't want to place an order", "place an order"),
                         ("we do not need to place an order", "place an order"),
                         ("别给 SKU-1 下单", "下单")):
        assert not is_question(text), text
        assert negated(text, phrase), text
