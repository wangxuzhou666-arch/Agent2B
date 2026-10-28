"""``twotrack demo`` runs end to end and shows what it promises."""

import io

from twotrack import cli
from twotrack.demo import Style, run_demo


def test_the_demo_walks_through_routing_the_lock_and_a_write(capsys):
    assert cli.main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "\033[" not in out  # not a terminal: plain text
    for marker in (
        "rule -> low_stock",  # keyword routing, with its source
        "semantic -> supplier_compare",  # the semantic fallback
        "fallback -> chat",  # nothing fits: chat, honestly offline
        "共",  # the Chinese question is answered in Chinese
        " ms",  # step timings
        "provenance",
        "mock template, numeric lock removed",  # the mock's narration is its template ...
        "answer from mock template",  # ... never credited to a model
        "mock draft",
        "Next period should come in around 31337.",  # the mock's invented figure (draft)
        "removed 1 sentence",  # ... which the numeric lock takes out
        # a complete write request: one phrase and every param it requires, so routing goes
        # straight to the write, with no routing question before its confirmation
        "rule -> create_po (0.9)",
        "every param it requires",
        "confirmation: Create a draft purchase order",  # the write waits
        "waits in the console's Approvals",
        "approves -> ok",
        "audit chain",
        "needs_confirmation -> #",
        "parent #",
        "all checks held",
    ):
        assert marker in out, marker
    assert "answer from model" not in out and "model draft" not in out
    assert "(create_po picked)" not in out  # the one-step path: nothing had to be picked
    assert not any(line.split()[:1] == ["narrate"] and " model," in line
                   for line in out.splitlines())
    answer_after_lock = next(line for line in out.splitlines() if "after lock" in line)
    assert "31337" not in answer_after_lock


def test_colour_only_when_asked_for():
    assert Style(False)("x", "bold") == "x"
    assert Style(True)("x", "bold").startswith("\033[1m")
    buffer = io.StringIO()
    assert run_demo(buffer, colour=True) == 0
    assert "\033[" in buffer.getvalue()
