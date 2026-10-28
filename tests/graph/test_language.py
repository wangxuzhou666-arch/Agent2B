"""Fixed sentences follow the request language; routing steps reach the pipeline for audit."""

from dataclasses import replace

from tests.fakes import PLUGIN, REGISTRY, FakePipeline, fake_lock, make_ctx
from twotrack.core import build_registry
from twotrack.core.graph import Engine, GraphSettings, run_query
from twotrack.core.i18n import say
from twotrack.providers import MockProvider


def ask(query, *roles, pipeline=None, registry=REGISTRY, **kw):
    engine = Engine(registry=registry, provider=MockProvider(), lock=fake_lock,
                    pipeline=pipeline or FakePipeline(registry),
                    settings=GraphSettings(max_query_chars=40))
    return run_query(engine, make_ctx(*(roles or ("operator",))), query, **kw)


def test_chat_fallback_answers_in_chinese():
    response = ask("写一首关于大海的诗")
    assert response.decision.kind.value == "chat"
    assert response.answer == say("offline_chat", "zh")


def test_clarification_prompt_in_chinese():
    response = ask("lead time 是多少")
    assert response.decision.kind.value == "clarify" and len(response.clarify_options) == 2
    assert response.answer == say("confirm_many", "zh")


TITLES = {"lead_time": {"en": "Lead time", "zh": "交货周期"},
          "delivery_delay": {"en": "Delivery delays", "zh": "交付延误"}}
TITLED = build_registry([replace(PLUGIN, tools=tuple(
    replace(t, title=TITLES[t.name]) if t.name in TITLES else t for t in PLUGIN.tools))])


def labels(response):
    return {o.name: o.label for o in response.clarify_options}


def test_every_clarification_option_is_labelled_with_its_title():
    # a tool without a title is named by its humanized id, in either language
    assert labels(ask("lead time 是多少")) == {"lead_time": "Lead time",
                                                "delivery_delay": "Delivery delay"}
    assert labels(ask("what is the lead time")) == {"lead_time": "Lead time",
                                                    "delivery_delay": "Delivery delay"}
    zh = ask("lead time 是多少", registry=TITLED)
    assert labels(zh) == {"lead_time": "交货周期", "delivery_delay": "交付延误"}
    en = ask("what is the lead time", registry=TITLED)
    assert labels(en) == {"lead_time": "Lead time", "delivery_delay": "Delivery delays"}
    assert all(o.label for o in zh.clarify_options + en.clarify_options)


def test_the_response_records_the_language_it_was_decided_in():
    assert ask("写一首关于大海的诗").language == "zh"
    assert ask("what is our stock level", "viewer").language == "en"
    assert ask("   ").language == "en"  # even a refused question has one


def test_confirmation_prompt_in_chinese_and_english():
    zh = ask("调整库存", explicit_target="stock_adjust", explicit_params={"qty": 3})
    assert zh.grant is not None and zh.answer.startswith("需要确认：")
    en = ask("adjust it", explicit_target="stock_adjust", explicit_params={"qty": 3})
    assert en.answer.startswith("Confirmation needed: ")


def test_template_narration_in_chinese_through_the_graph():
    response = ask("现在 stock level 是多少", "viewer")
    assert response.decision.target == "stock_level"
    assert response.answer.startswith("共 2 条记录") and "15543" in response.answer
    narrate = next(s for s in response.trace if s.step == "narrate")
    assert narrate.facts["language"] == "zh"


def test_the_narrate_step_names_the_provider_behind_the_text():
    response = ask("what is our stock level", "viewer")
    narrate = next(s for s in response.trace if s.step == "narrate")
    # the source stays "model" for compatibility; provider and mock say it was the template
    assert response.answer_source == "model" and narrate.facts["source"] == "model"
    assert (narrate.facts["provider"], narrate.facts["mock"]) == ("mock", True)


def test_ingress_errors_follow_the_language():
    assert ask("库" * 41).error == say("query_too_long", "zh", limit=40)
    assert ask("   ").error == say("query_empty", "en")


def test_the_routing_ladder_is_handed_to_the_pipeline():
    pipeline = FakePipeline()
    ask("what is our stock level", "viewer", pipeline=pipeline)
    assert pipeline.priors == [["candidates", "understand"]]
