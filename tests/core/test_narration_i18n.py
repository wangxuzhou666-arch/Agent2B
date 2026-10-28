"""Readable template narration in the request language, behind the same numeric lock."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from twotrack.core import (
    Plugin,
    Provenance,
    RegistryError,
    Tool,
    ToolOutputs,
    ToolResult,
    build_registry,
)
from twotrack.core.guards.numeric_lock import lock_narrative
from twotrack.core.i18n import detect_language, language_of, say
from twotrack.core.narration import (
    aggregate_label,
    humanize_key,
    narrate_result,
    result_view,
    template_narration,
)
from twotrack.providers import ChatMessage, MockProvider, OpenAICompatProvider

RESULT = ToolResult(
    rows=[{"po": "x"}] * 15, aggregates={"late_count": 15, "oldest_days_late": 66,
                                         "class_a_count": 3, "estimated_cost": 1250.5},
    provenance=Provenance(tool="late", row_count=15, as_of=datetime(2026, 10, 1, tzinfo=UTC),
                          scope="open orders, ETA before 2026-10-01"))
LABELS = {"en": {"late_count": "Late orders", "oldest_days_late": "Longest delay (days)"},
          "zh": {"late_count": "逾期采购单", "oldest_days_late": "最长逾期（天）"}}


@pytest.mark.parametrize(("key", "label"), [
    ("class_a_count", "Class A count"),
    ("po_count", "PO count"),
    ("skus_to_order", "SKUs to order"),
    ("lead_time_days", "Lead time (days)"),
    ("oldest_days_late", "Oldest days late"),
    ("contract_discount_pct", "Contract discount (%)"),
    ("count", "Count"),
])
def test_keys_without_labels_are_humanized(key, label):
    assert humanize_key(key) == label


@pytest.mark.parametrize(("text", "language"), [
    ("which SKUs are low on stock", "en"),
    ("哪些采购单逾期了", "zh"),
    ("SKU-10042 库存多少", "zh"),  # an identifier is one word against four characters
    ("给SKU-10042下单200件", "zh"),
    ("Show me the 库存 for SKU-10042", "en"),
    ("", "en"),
])
def test_the_reply_language_follows_the_query(text, language):
    assert detect_language(text) == language


def test_a_recorded_language_wins_over_detection():
    assert language_of({"query": "哪些采购单逾期了", "language": "en"}) == "en"
    assert language_of({"query": "哪些采购单逾期了", "language": "fr"}) == "zh"


def test_labels_fall_back_from_language_to_english_to_the_humanized_key():
    assert aggregate_label("late_count", "zh", LABELS) == "逾期采购单"
    assert aggregate_label("late_count", "zh", {"en": LABELS["en"]}) == "Late orders"
    assert aggregate_label("class_a_count", "zh", LABELS) == "Class A count"


def test_english_template_reads_like_sentences_and_passes_the_lock():
    text = template_narration(result_view(RESULT, language="en", labels=LABELS))
    assert text.startswith("15 rows matched (open orders, ETA before 2026-10-01).")
    assert "Late orders: 15." in text and "Longest delay (days): 66." in text
    assert "Class A count: 3." in text and "Estimated cost: 1250.5." in text
    assert "_" not in text  # no raw keys
    assert lock_narrative(text, RESULT.allowed_numbers()).stripped_count == 0


def test_chinese_template_and_the_lock():
    text = template_narration(result_view(RESULT, language="zh", labels=LABELS))
    assert text.startswith("共 15 条记录（open orders, ETA before 2026-10-01）。")
    assert "逾期采购单：15。" in text and "最长逾期（天）：66。" in text
    assert lock_narrative(text, RESULT.allowed_numbers()).stripped_count == 0
    empty = ToolResult(provenance=Provenance(tool="t", row_count=0, as_of=RESULT.provenance.as_of))
    assert template_narration(result_view(empty, language="zh")) == "没有匹配的记录。"


@pytest.mark.parametrize("language", ["en", "zh"])
def test_the_lock_removes_an_invented_figure_in_both_languages(language):
    narration = narrate_result(MockProvider(hallucinated_number=31337), "q", RESULT,
                               lock_narrative, language=language, labels=LABELS)
    assert narration.source == "model" and narration.stripped == 1
    assert "31337" not in narration.text
    assert narration.text == template_narration(
        result_view(RESULT, language=language, labels=LABELS))


def test_the_offline_mock_answers_in_the_users_language():
    mock = MockProvider()
    assert mock.chat([ChatMessage(role="user", content="写一首关于大海的诗")]) == \
        say("offline_chat", "zh")
    assert "No language model" in mock.chat([ChatMessage(role="user", content="a poem")])
    assert mock.agent_step([ChatMessage(role="user", content="帮我总结一下")], []).text == \
        say("offline_agent", "zh")


def test_the_narrator_prompt_names_the_reply_language_and_the_labels():
    sent = []

    def create(**kwargs):
        sent.append(kwargs)
        message = SimpleNamespace(content="逾期采购单共 15 张。", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    provider = OpenAICompatProvider(model="m", client=client)
    narration = narrate_result(provider, "哪些采购单逾期了", RESULT, lock_narrative,
                               language="zh", labels=LABELS)
    assert narration.text == "逾期采购单共 15 张。" and narration.stripped == 0
    system, user = sent[0]["messages"]
    assert "reply language" in system["content"] and "label" in system["content"]
    payload = json.loads(user["content"])["result"]
    assert payload["language"] == "zh" and payload["labels"]["late_count"] == "逾期采购单"
    assert "rows" not in payload  # still only the narrator view


def test_labels_in_an_unknown_language_stop_the_registry():
    bad = Tool(name="t", description="d", roles=("viewer",), effect="read", approval="none",
               outputs=ToolOutputs(aggregates=("late_count",), columns=()), explorable=False,
               params_model=type(RESULT), handler=lambda ctx, p: RESULT,
               aggregate_labels={"cn": {"late_count": "x"}, "zh": {"late_count": ""}})
    with pytest.raises(RegistryError) as raised:
        build_registry([Plugin(name="p", version="1", tools=(bad,))])
    text = str(raised.value)
    assert "language 'cn' is not supported" in text and "non-empty labels" in text
