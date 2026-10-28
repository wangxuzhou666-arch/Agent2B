"""The scope guard over the API: a clarification carries exactly what the console sends back,
and the acknowledged run is answered, audited and marked unfiltered."""

from twotrack.core import SCOPE_ACK_PARAM

# the fixture's `bolt` category extends `item`; item_count cannot filter by category
NARROW = {"query": "how many items are bolts"}


def ask(api, body, user="olga"):
    response = api.post("/api/query?stream=false", user, json=body)
    assert response.status_code == 200
    return response.json()


def test_a_question_narrower_than_the_report_gets_choices_not_a_number(api):
    body = ask(api, NARROW)
    assert body["decision"]["kind"] == "clarify" and body["aggregates"] == {}
    # a target without a declared title is named by its humanized name
    assert body["answer"].startswith("“Item count” can't be limited to category Bolt.")
    (option,) = body["clarify_options"]  # nothing else can filter by category either
    assert option["name"] == "item_count" and option["unfiltered"] is True
    assert option["label"] == "Show all categories"
    assert option["explicit_params"] == {SCOPE_ACK_PARAM: ["category:item=bolt"]}
    scope = body["decision"]["scope"]
    assert scope["unbound"][0]["value"] == "bolt" and scope["acknowledged"] is False
    assert "scope" in [s["step"] for s in body["trace"]]


def test_the_acknowledged_choice_runs_once_and_is_marked_unfiltered(api):
    option = ask(api, NARROW)["clarify_options"][0]
    body = ask(api, {**NARROW, "explicit_target": option["name"],
                     "explicit_params": option["explicit_params"]})
    assert body["decision"]["kind"] == "tool" and body["aggregates"] == {"item_count": 6}
    assert body["provenance"]["scope"].endswith("not filtered by category Bolt; all categories "
                                                "shown")
    entry = api.get(f"/api/audit/{body['audit_ids'][-1]}", "ada").json()
    scope = next(s for s in entry["trace"] if s["step"] == "scope")
    assert scope["facts"]["verdict"] == "unfiltered" and scope["facts"]["acknowledged"]
    assert SCOPE_ACK_PARAM not in entry["params"]  # never reaches the tool or the record
    stats = api.get("/api/audit/stats", "ada").json()
    assert stats["by_status"] == {"clarify": 1, "ok": 1}
