"""Numeric lock: unbacked numbers cost their whole sentence; everything else is kept."""

import pytest

from twotrack.core import Provenance, ToolResult
from twotrack.core.guards import lock_narrative, split_sentences


def kept(text, allowed):
    return lock_narrative(text, set(allowed)).text


def test_backed_numbers_pass_unchanged():
    text = "There are 42 open lines. Their value is $1,234.50 in total."
    result = lock_narrative(text, {42, 1234.5})
    assert result.text == text and result.stripped_count == 0


def test_unbacked_sentence_is_stripped_and_reported():
    result = lock_narrative("There are 42 open lines. About 17 are late! Ask me more.", {42})
    assert result.text == "There are 42 open lines. Ask me more."
    assert result.stripped_count == 1 and result.stripped_sentences == ["About 17 are late!"]


def test_small_integers_are_not_exempt():
    assert kept("3 suppliers are late.", {42}) == ""


@pytest.mark.parametrize(("written", "ok"), [
    ("1,234.6", True), ("1,235", True), ("1.2 thousand", True), ("1.23k", True),
    ("1,240", False), ("1,234.57", True), ("1,234.55", False),
])
def test_rounding_to_written_precision(written, ok):
    assert (kept(f"Total is {written}.", {1234.567}) != "") is ok


def test_scaled_millions():
    assert kept("Revenue reached 1.2 million.", {1_234_567}) != ""
    assert kept("Revenue reached 1.3 million.", {1_234_567}) == ""


@pytest.mark.parametrize(("written", "ok"), [("93.5%", True), ("94%", True), ("95%", False),
                                             ("93.5 percent", True)])
def test_percent_matches_ratio_or_percent_value(written, ok):
    assert (kept(f"Fill rate was {written}.", {0.935}) != "") is ok
    assert (kept(f"Fill rate was {written}.", {93.5}) != "") is ok


def test_sign_is_ignored():
    assert kept("Stock fell by 12 units.", {-12}) != ""


def test_year_exempt_but_separated_quantity_checked():
    assert kept("Since 2019, stock grew to 42.", {42}) == "Since 2019, stock grew to 42."
    assert kept("We hold 2,019 units.", {42}) == ""
    assert kept("Spend was $2019.", {42}) == ""  # currency: not a year


def test_dates_ordinals_identifiers_and_list_markers_are_not_quantities():
    text = ("As of 2026-10-26 09:30, part SKU-123 in Q3 ranks 3rd with 42 units.\n"
            "1. The #2 site holds 42.")
    assert kept(text, {42}) == text


def test_cjk_punctuation_splits_sentences():
    result = lock_narrative("数值是42。另一个是17！最后一句；", {42})
    assert result.text == "数值是42。最后一句；"
    assert result.stripped_sentences == ["另一个是17！"]


def test_cjk_numerals_and_full_width_digits():
    assert kept("总数为四十二。", {42}) == "总数为四十二。"
    assert kept("总数为五十。", {42}) == ""
    assert kept("总数为４２。", {42}) == "总数为４２。"
    assert kept("约有3万。", {30_000}) == "约有3万。"
    assert kept("占百分之五十。", {0.5}) == "占百分之五十。"
    assert kept("第一个仓库。", set()) == "第一个仓库。"


def test_newlines_keep_paragraphs():
    text = "Line one has 42.\nLine two has 7.\nLine three."
    assert kept(text, {42}) == "Line one has 42.\n\nLine three."


def test_split_keeps_decimals_together():
    assert split_sentences("Value 3.5 here. Next") == ["Value 3.5 here. ", "Next"]


def test_works_with_tool_result_allowed_numbers():
    result = ToolResult(rows=[{"sku": "A"}, {"sku": "B"}], aggregates={"late_ratio": 0.5},
                        provenance=Provenance(tool="t", row_count=2, as_of="2026-10-01T00:00Z"))
    text = "2 rows matched. Half (50%) are late. 9 are missing."
    assert kept(text, result.allowed_numbers()) == "2 rows matched. Half (50%) are late."
