"""End-to-end dry runs: the scorer catches injected wrong numbers, the lock strips them, twotrack
on the mock shows none, the report renders and result files never hold a secret."""

import json

import pytest
from bench.fake_llm import MockEquivalentClient, ScriptedLLM
from bench.metering import MeteredClient
from bench.report import render
from bench.run import REFUSED, RunConfig, dump_results, main, run_benchmark, write_results

# Shaped unlike any real key on purpose (the leak scan must not flag the test itself).
SENTINEL = "bench_sentinel_value_7f3a9c51d2"


def records(results, view):
    return [r for r in results["records"] if r["view"] == view]


def run_with(scripted: ScriptedLLM, **kwargs):
    """A dry run whose baseline client is the given scripted fake (so tests can read what it
    injected)."""
    return run_benchmark(RunConfig(client_factory=lambda system: scripted, **kwargs))


@pytest.fixture(scope="module")
def append_run():
    fake = ScriptedLLM(hallucination_rate=1.0, mode="append", seed=1)
    return fake, run_with(fake, systems=("A", "C"))


@pytest.fixture(scope="module")
def mock_run():
    return run_benchmark(RunConfig(systems=("B",)))


def test_every_injected_number_is_caught_in_A_and_stripped_in_C(append_run):
    fake, results = append_run
    answerable = {r["item_id"] for r in records(results, "A") if r["answerable"]}
    assert set(fake.injected) == answerable  # rate 1.0: every answerable item
    for record in records(results, "A"):
        injected = fake.injected.get(record["item_id"])
        flagged = [n for n in record["score"]["numbers"] if n["role"] == "unsupported"]
        if injected is None:
            assert not record["score"]["wrong_number"], record["item_id"]
        else:
            assert record["score"]["wrong_number"], record["item_id"]
            assert [n["value"] for n in flagged] == [injected], record["item_id"]
    for record in records(results, "C"):
        assert record["score"]["unsupported"] == 0, record["item_id"]
        assert not record["score"]["wrong_number"], record["item_id"]
        injected = fake.injected.get(record["item_id"])
        if injected is not None:
            assert all(n["value"] != injected for n in record["score"]["numbers"])
            assert record["detail"]["stripped"] >= 1


def test_no_false_alarms_without_injection():
    results = run_with(ScriptedLLM(hallucination_rate=0.0), systems=("A",))
    for record in records(results, "A"):
        assert not record["score"]["wrong_number"], record["item_id"]
        assert record["score"]["outcome"] == "correct", record["item_id"]


def test_replaced_answers_are_wrong_in_A_and_withheld_in_C():
    fake = ScriptedLLM(hallucination_rate=1.0, mode="replace", seed=2)
    results = run_with(fake, systems=("A", "C"), category="lookup")
    for a, c in zip(records(results, "A"), records(results, "C"), strict=True):
        if a["item_id"] in fake.injected:
            assert a["score"]["outcome"] == "wrong_number"
            assert c["score"]["outcome"] in ("abstained", "incomplete")
            assert not c["score"]["wrong_number"]


def test_wrong_scope_answers_are_contradictions():
    fake = ScriptedLLM(hallucination_rate=1.0, mode="distractor", seed=3)
    results = run_with(fake, systems=("A",))
    hits = [r for r in records(results, "A") if r["item_id"] in fake.injected]
    assert len(hits) >= 30
    for record in hits:
        assert record["score"]["wrong_number"], record["item_id"]
        assert record["score"]["contradiction"], record["item_id"]
        assert any(n["distractor_of"] for n in record["score"]["numbers"]), record["item_id"]


def test_twotrack_on_the_mock_never_shows_an_unsupported_number(mock_run):
    for view in ("B_seen", "B_text"):
        for record in records(mock_run, view):
            assert record["score"]["unsupported"] == 0, (view, record["item_id"])
    summary = mock_run["summary"]
    assert summary["B_seen"]["correct"] >= summary["B_text"]["correct"]
    assert summary["B_seen"]["routing"]["n"] == 75
    assert mock_run["meta"]["data"]["unchanged"] is True


def test_twotrack_lock_strips_a_hallucinating_narrator():
    results = run_benchmark(RunConfig(systems=("B",), category="aggregation",
                                      mock_hallucinated_number=987654))
    executed = [r for r in records(results, "B_text") if r["detail"]["rows_shown"]]
    assert executed
    for record in executed:
        assert record["detail"]["narrative_stripped"] >= 1
        assert "987654" not in record["answer"] and record["score"]["unsupported"] == 0


def test_report_renders_every_section(append_run, mock_run):
    _, results = append_run
    text = render(results)
    assert "DRY RUN" in text and "| System |" in text and "A  baseline agent" in text
    assert "C  baseline + numeric lock" in text and "### By category" in text
    text = render(mock_run)
    assert "**(headline)**" in text and "B  twotrack, narration only" in text


def test_result_files_hold_no_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("TWOTRACK_LLM_API_KEY", SENTINEL)
    clients = {"A": ScriptedLLM(hallucination_rate=0.5), "B": MockEquivalentClient()}
    config = RunConfig(provider="openai_compat", model="offline-test",
                       base_url="https://bench.invalid/v1/secret-path", api_key=SENTINEL,
                       limit=6, client_factory=clients.__getitem__)
    results = run_benchmark(config)
    path = write_results(results, tmp_path / "result.json", [SENTINEL])
    text = path.read_text(encoding="utf-8")
    assert SENTINEL not in text and "secret-path" not in text
    assert json.loads(text)["meta"]["endpoint_host"] == "bench.invalid"
    assert "api_key" not in text
    # B went through the real OpenAICompatProvider with metered calls: usage was recorded
    assert any(r["calls"] for r in records(results, "B_seen"))


def test_dump_scrubs_a_secret_that_slipped_into_a_field():
    text = dump_results({"records": [{"answer": f"key={SENTINEL}"}]}, [SENTINEL])
    assert SENTINEL not in text and "[REDACTED]" in text


def test_real_run_refuses_without_confirm(monkeypatch, capsys):
    monkeypatch.setenv("TWOTRACK_LLM_MODEL", "some-model")
    monkeypatch.setenv("TWOTRACK_LLM_API_KEY", SENTINEL)
    code = main(["--provider", "openai_compat", "--limit", "3", "--quiet"])
    out = capsys.readouterr().out
    assert code == REFUSED and "--confirm" in out and SENTINEL not in out


def test_metered_client_pins_temperature_and_records_usage():
    seen = {}

    class Inner:
        def __init__(self):
            self.chat = self
            self.completions = self

        def create(self, **kwargs):
            seen.update(kwargs)
            return MockEquivalentClient().create(**kwargs)

    metered = MeteredClient(Inner())
    metered.chat.completions.create(model="m", temperature=0.2,
                                    messages=[{"role": "user", "content": "hello"}])
    (call,) = metered.take()
    assert seen["temperature"] == 0.0 and call.ok and call.prompt_tokens > 0 and call.estimated
    assert metered.take() == []
