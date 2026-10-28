"""The budget estimator measures real prompt payloads offline and prices them when it can."""

from bench.estimate import measure, plan_text, render_estimate, summarize_calls
from bench.run import RunConfig


def test_measured_prompts_and_priced_table(items):
    sample = items[:5]
    estimates = summarize_calls(measure(sample), len(sample))
    assert estimates["A"].calls >= 5 and estimates["B"].calls >= 5
    assert estimates["A"].prompt_tokens > 0 and estimates["B"].prompt_tokens > 0
    high_prompt, _ = estimates["A"].high()
    assert high_prompt > estimates["A"].prompt_tokens
    priced = {"model-x": {"input_per_mtok": 1.0, "output_per_mtok": 2.0}}
    table = render_estimate(estimates, models=["model-x", "model-y"], prices=priced)
    assert "model-x" in table and "$" in table
    assert "n/a / n/a" in table  # model-y has no price entry: never guessed


def test_plan_text_for_a_real_run_is_offline():
    text = plan_text(RunConfig(provider="openai_compat", model="m", limit=4))
    assert "Planned real run" in text and "4 questions" in text
