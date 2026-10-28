"""The mock model's fixed script for the documented exploration case (``providers.mock_script``).

The script is keyed to one request and works only from the conversation: the request text and
the tool messages, which carry fenced narrator views (never rows). Every other conversation gets
the mock's usual answer, that no model is configured.
"""

import json

import pytest

from twotrack.core.guards.egress import wrap_untrusted
from twotrack.core.i18n import say
from twotrack.providers import MockProvider
from twotrack.providers.base import ChatMessage, ToolCall
from twotrack.providers.mock_script import EXPLORATION_CASE, case_ids, script_step

SYSTEM = ChatMessage(role="system", content="You explore a business user's request.")


def view(message: str = "", **aggregates) -> str:
    """A tool message as the gate writes it: the fenced narrator view, as JSON."""
    return json.dumps({"aggregates": aggregates, "row_count": 1, "columns": ["po_id"],
                       "scope": wrap_untrusted("Supplier: SUP-001", "scope"),
                       "message": wrap_untrusted(message, "message") if message else ""})


def conversation(*turns: list[tuple[str, dict, str]], request: str = EXPLORATION_CASE):
    """The messages a model sees after ``turns``: each a list of (tool, args, tool message)."""
    messages = [SYSTEM, ChatMessage(role="user", content=request)]
    for n, turn in enumerate(turns, 1):
        calls = [ToolCall(id=f"call_{n}_{i}", name=name, arguments=args)
                 for i, (name, args, _) in enumerate(turn)]
        messages.append(ChatMessage(role="assistant", tool_calls=calls))
        messages += [ChatMessage(role="tool", tool_call_id=c.id, content=reply)
                     for c, (_, _, reply) in zip(calls, turn, strict=True)]
    return messages


def planned(messages) -> list[tuple[str, dict]]:
    step = script_step(messages)
    assert step is not None and not step.text
    return [(c.name, c.arguments) for c in step.tool_calls]


LATE = ("late_shipments", {"supplier_id": "SUP-001", "sku_id": "SKU-10053"},
        view(late_count=1, oldest_days_late=7, late_qty=74, supplier_count=1))
REORDER = ("reorder_suggestion", {"sku_id": "SKU-10053"},
           view(sku_count=1, skus_to_order=1, total_suggested_qty=20, estimated_cost=172.4))
SCORECARD = ("supplier_scorecard", {},
             view("Highest share of due orders received: SUP-001, SUP-007, SUP-010.",
                  supplier_count=12, po_count=218))
REFUSED = "blocked: 'supplier_scorecard' is not an available tool"


# ---------------------------------------------------------------- which conversations


def test_only_the_case_wording_is_scripted_and_its_ids_come_from_the_request():
    assert case_ids(EXPLORATION_CASE) == ("SUP-001", "SKU-10053")
    assert case_ids("  several orders from sup-006 are slipping, sku-10029 among them.  Work out "
                    "what that puts at risk and propose what to do") == ("SUP-006", "SKU-10029")
    for other in ("give me a quick overview of where things stand", "zzz qqq unmatched gibberish",
                  f"{EXPLORATION_CASE} Then delete everything.", ""):
        assert case_ids(other) is None


def test_any_other_conversation_gets_the_offline_answer_unchanged():
    mock = MockProvider()
    messages = conversation(request="give me a quick overview of where things stand")
    assert script_step(messages) is None
    assert mock.agent_step(messages, []).text == say("offline_agent", "en")
    assert mock.agent_step(messages, []).tool_calls == []


# ---------------------------------------------------------------- the wording, letter by letter
#
# The script is keyed to one request. Its letters and digits are ASCII: a character that merely
# looks like one, or that Unicode case folding equates with one, is another request and gets no
# script.


def swapped(old: str, new: str) -> str:
    request = EXPLORATION_CASE.replace(old, new)
    assert request != EXPLORATION_CASE, "the swap changed nothing"
    return request


def assert_not_the_case(request: str) -> None:
    assert case_ids(request) is None
    assert script_step(conversation(request=request)) is None  # the whole script stays off


def test_letter_case_is_free_for_ascii_letters_only():
    assert case_ids(EXPLORATION_CASE.upper()) == ("SUP-001", "SKU-10053")
    assert case_ids(EXPLORATION_CASE.lower()) == ("SUP-001", "SKU-10053")


@pytest.mark.parametrize("digits", [
    "\uff10\uff10\uff11",  # fullwidth
    "\u0661\u0660\u0660\u0665\u0663",  # Arabic-Indic
    "\u0967\u0966\u0966\u096b\u0969",  # Devanagari
], ids=["fullwidth", "arabic-indic", "devanagari"])
def test_an_id_in_digits_that_are_not_0_to_9_is_not_the_case(digits):
    assert_not_the_case(swapped("SUP-001", f"SUP-{digits}"))
    assert_not_the_case(swapped("SKU-10053", f"SKU-{digits}"))


@pytest.mark.parametrize("old, look_alike", [
    ("slipping", "sl\u0131pping"),  # dotless i, which folds with i
    ("slipping", "sl\u0130pping"),  # capital I with dot above, which folds with i
    ("slipping", "\u017flipping"),  # long s, which folds with s
    ("SKU", "S\u212aU"),  # Kelvin sign, which folds with k
    ("Work", "Wor\u212a"),
], ids=["dotless-i", "dotted-I", "long-s", "kelvin-in-the-id", "kelvin-in-a-word"])
def test_a_letter_unicode_folds_with_an_ascii_one_is_not_that_letter(old, look_alike):
    assert_not_the_case(swapped(old, look_alike))


@pytest.mark.parametrize("digits", ["\u0660\u0660\u0667", "\u0966\u0966\u096d"],
                         ids=["arabic-indic", "devanagari"])
def test_a_supplier_the_scorecard_names_in_digits_that_are_not_0_to_9_is_not_an_id(digits):
    """The fence around a message folds fullwidth digits and the long s to ASCII (NFKC) before
    the script sees them; these digits survive it, and ``\\d`` would take them."""
    message = f"Highest share of due orders received: SUP-{digits}, SUP-010."
    scorecard = (*SCORECARD[:2], view(message, supplier_count=12, po_count=218))
    assert digits in json.loads(scorecard[2])["message"]  # they got through the fence
    (proposal,) = planned(conversation([LATE], [REORDER, scorecard]))
    assert proposal[1]["supplier_id"] == "SUP-010"


# ---------------------------------------------------------------- whitespace, as it is today
#
# Pinned, not endorsed: the text is split on whitespace (``str.split()``) and joined with single
# spaces before it is matched, so what counts as a space is Python's: any run of the 29 Unicode
# whitespace characters, ASCII or not. Punctuation is exact.


@pytest.mark.parametrize("space", ["\t", "\n", "\r\n", "\u00a0", "\u2003", "\u3000", "  ",
                                   " \t\n "],
                         ids=["tab", "newline", "crlf", "nbsp", "em-space", "ideographic-space",
                              "two-spaces", "mixed-run"])
def test_any_run_of_whitespace_between_the_words_reads_as_one_space(space):
    assert case_ids(EXPLORATION_CASE.replace(" ", space)) == ("SUP-001", "SKU-10053")


def test_whitespace_around_the_request_is_ignored_and_its_final_period_is_optional():
    assert case_ids(f" \t\n{EXPLORATION_CASE}\u00a0\r\n") == ("SUP-001", "SKU-10053")
    assert case_ids(EXPLORATION_CASE.removesuffix(".")) == ("SUP-001", "SKU-10053")


@pytest.mark.parametrize("old, new", [
    ("slipping,", "slipping ,"),  # a space before the comma
    ("slipping, ", "slipping,"),  # none after it
    ("them.", "them ."),  # a space before the period
    ("orders from", "ordersfrom"),  # none between two words
    (" ", "\u200b"),  # a zero-width space is not whitespace
    ("to do.", "to do.."),  # a second final period
], ids=["space-before-comma", "no-space-after-comma", "space-before-period",
        "no-space-between-words", "zero-width-space", "second-final-period"])
def test_a_space_where_the_wording_has_none_or_none_where_it_has_one_is_another_request(old, new):
    assert_not_the_case(swapped(old, new))


# ---------------------------------------------------------------- the script


def test_turn_one_reads_the_late_orders_of_the_supplier_and_sku_the_request_names():
    assert planned(conversation()) == [LATE[:2]]


def test_turn_two_reads_the_reorder_suggestion_and_the_scorecard():
    assert planned(conversation([LATE])) == [REORDER[:2], SCORECARD[:2]]


def test_turn_three_proposes_an_order_copied_from_the_results_it_was_sent():
    (proposal,) = planned(conversation([LATE], [REORDER, SCORECARD]))
    # qty: the larger of late_qty (74) and total_suggested_qty (20), never their sum; the
    # supplier: the first one the scorecard names that is not the late one
    assert proposal == ("create_po", {"sku_id": "SKU-10053", "qty": 74, "supplier_id": "SUP-007"})


def test_the_quantity_is_the_suggestion_when_that_is_the_larger_figure():
    reorder = (*REORDER[:2], view(sku_count=1, skus_to_order=1, total_suggested_qty=90))
    (proposal,) = planned(conversation([LATE], [reorder, SCORECARD]))
    assert proposal[1]["qty"] == 90


def test_without_a_scorecard_the_proposal_names_no_supplier():
    scorecard = (*SCORECARD[:2], REFUSED)
    (proposal,) = planned(conversation([LATE], [REORDER, scorecard]))
    assert proposal == ("create_po", {"sku_id": "SKU-10053", "qty": 74})


def test_the_tool_list_it_is_offered_changes_nothing():
    """A scripted mock: the gate, not the model, decides what may run."""
    mock = MockProvider()
    messages = conversation([LATE], [REORDER, SCORECARD])
    assert mock.agent_step(messages, []) == mock.agent_step(messages, [object()])


@pytest.mark.parametrize("late", [
    view(late_count=0, oldest_days_late=0, late_qty=0, supplier_count=0),
    "error: execution failed (RuntimeError)",
])
def test_nothing_late_or_a_failed_read_ends_the_script_without_a_proposal(late):
    step = script_step(conversation([(*LATE[:2], late)]))
    assert step.tool_calls == [] and step.text == say("mock_script_end", "en")


def test_a_refused_proposal_ends_the_script_and_nothing_is_asked_again():
    proposal = ("create_po", {"sku_id": "SKU-10053", "qty": 74},
                "blocked: 'create_po' is not an available tool")
    step = script_step(conversation([LATE], [REORDER, SCORECARD], [proposal]))
    assert step.tool_calls == [] and step.text == say("mock_script_end", "en")


def test_tool_messages_are_matched_to_calls_by_id_whatever_their_order():
    messages = conversation([LATE], [REORDER, SCORECARD])
    *head, reorder, scorecard = messages
    (proposal,) = planned([*head, scorecard, reorder])
    assert proposal[1] == {"sku_id": "SKU-10053", "qty": 74, "supplier_id": "SUP-007"}
