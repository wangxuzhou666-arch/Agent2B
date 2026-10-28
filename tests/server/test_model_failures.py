"""The API answer of a configured model that fails says so: ``model_errors`` on the answer, the
failed call in the trace and in the audit record. The zero-key mock reports none."""

from types import SimpleNamespace

from tests.server.conftest import Api, parse_sse
from twotrack.providers import OpenAICompatProvider

COUNT = {"query": "how many items do we have"}
WRITE = {"query": "set it", "explicit_target": "set_item_qty",
         "explicit_params": {"sku": "A-1", "qty": 12}}
REFUSED = {"error": "BadRequestError", "http_status": 400}


class BadRequestError(Exception):
    status_code = 400


class RefusingClient:
    """Every call is refused with HTTP 400, as a model that rejects the temperature it got."""

    def __init__(self) -> None:
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        raise BadRequestError("rejected")


def refusing_api(make_runtime) -> Api:
    return Api(make_runtime(provider=OpenAICompatProvider(model="a-model",
                                                          client=RefusingClient())))


def test_the_answer_names_each_failed_model_call(make_runtime):
    api = refusing_api(make_runtime)
    body = api.post("/api/query?stream=false", "vera", json=COUNT).json()
    assert body["answer_source"] == "template" and body["answer"] and body["error"] is None
    assert body["model_errors"] == [{"call": "route", **REFUSED}, {"call": "narrate", **REFUSED}]
    narrate = next(s for s in body["trace"] if s["step"] == "narrate")
    assert narrate["facts"]["model_error"] == {"call": "narrate", **REFUSED}
    assert narrate["facts"]["mock"] is False
    # the stream's response frame carries the same, and its trace frames the same facts
    frames = parse_sse(api.post("/api/query", "vera", json=COUNT).text)
    (response,) = [data for event, data in frames if event == "response"]
    assert response["model_errors"] == body["model_errors"]
    respond = [data for event, data in frames if event == "trace" and data["step"] == "respond"]
    assert respond[0]["facts"]["model_errors"] == body["model_errors"]


def test_the_zero_key_mock_reports_no_failed_call(api):
    body = api.post("/api/query?stream=false", "vera", json=COUNT).json()
    assert body["model_errors"] == []
    narrate = next(s for s in body["trace"] if s["step"] == "narrate")
    assert narrate["facts"]["mock"] is True and "model_error" not in narrate["facts"]


def test_an_approved_write_says_its_narration_fell_back(make_runtime):
    api = refusing_api(make_runtime)
    grant_id = api.post("/api/query?stream=false", "olga", json=WRITE).json()["approval"][
        "grant_id"]
    done = api.post(f"/api/approvals/{grant_id}/approve", "olga").json()
    assert done["status"] == "ok" and done["answer_source"] == "template" and done["answer"]
    assert done["model_errors"] == [{"call": "narrate", **REFUSED}]
    record = api.get(f"/api/audit/{done['audit_ids'][-1]}", "ada").json()
    steps = {s["step"]: s["facts"] for s in record["trace"]}
    assert steps["narrate"]["model_error"] == {"call": "narrate", **REFUSED}
    assert steps["respond"]["model_errors"] == [{"call": "narrate", **REFUSED}]
