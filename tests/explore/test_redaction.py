"""A model key is redacted in every form it can take in text: as it is, JSON-escaped and
URL-quoted. The host does it with ``pi.redact`` and the worker with ``secretForms`` in
harness/pi/worker.mjs; both use the same forms and the same minimum length. (That a run really
keeps the key out of every line, log and answer is ``test_pi_runs`` and ``test_fake_worker``.)
"""

import json
import re
import subprocess
import urllib.parse

import pytest

from twotrack.core.explore.pi import (
    DEFAULT_HARNESS_DIR,
    MIN_SECRET_CHARS,
    REDACTED,
    redact,
    secret_forms,
)

WORKER = DEFAULT_HARNESS_DIR / "worker.mjs"
# changes under JSON-escaping (the quotes, the backslash, é) and under URL-quoting (the space,
# / + ! ( ) and é), so that no two of its forms coincide
KEY = 'sk-test "q"\\é/+!(x)~-0123456789'


def test_the_forms_are_the_ones_json_and_url_encoders_write():
    assert secret_forms(KEY) == {
        KEY,
        json.dumps(KEY)[1:-1],  # Python's json: non-ASCII as \uXXXX
        json.dumps(KEY, ensure_ascii=False)[1:-1],  # JavaScript's JSON.stringify
        urllib.parse.quote(KEY, safe=""),  # every character but the unreserved ones
        urllib.parse.quote(KEY, safe="!'()*"),  # JavaScript's encodeURIComponent
    }
    assert len(secret_forms(KEY)) == 5


@pytest.mark.parametrize("form", sorted(secret_forms(KEY)))
def test_each_form_is_replaced_wherever_it_appears(form):
    text = "401 Incorrect API key provided: {}. Retry with {}"
    assert redact(text.format(form, form), [KEY]) == text.format(REDACTED, REDACTED)


def test_text_that_holds_no_key_is_left_alone():
    assert redact("nothing to hide", [KEY, ""]) == "nothing to hide"
    assert redact("nothing to hide", []) == "nothing to hide"


def test_a_key_shorter_than_the_minimum_is_a_placeholder_and_is_not_redacted():
    short, enough = "e" * (MIN_SECRET_CHARS - 1), "f" * MIN_SECRET_CHARS
    assert secret_forms(short) == set()
    assert redact(f"model {short} ready", [short]) == f"model {short} ready"
    assert redact(f"model {enough} ready", [enough]) == f"model {REDACTED} ready"


def test_the_worker_keeps_the_same_minimum():
    found = re.search(r"^const MIN_SECRET_CHARS = (\d+);$", WORKER.read_text(encoding="utf-8"),
                      re.MULTILINE)
    assert found and int(found[1]) == MIN_SECRET_CHARS


@pytest.mark.pi
def test_the_worker_computes_the_same_forms_as_the_host(pi_harness):
    """Runs the worker's own ``secretForms`` source under Node on keys that tell the forms
    apart, and compares what it returns with ``secret_forms``."""
    source = WORKER.read_text(encoding="utf-8")
    function = re.search(r"^function secretForms\(secret\) \{\n.*?^\}\n", source,
                         re.MULTILINE | re.DOTALL)
    assert function, "worker.mjs has no secretForms function"
    keys = [KEY, "sk-plain-0123456789abcdef", "e" * (MIN_SECRET_CHARS - 1),  # gitleaks:allow
            "é" * MIN_SECRET_CHARS, "\U0001f511" * (MIN_SECRET_CHARS - 1),
            "\U0001f511" * MIN_SECRET_CHARS, "tab\tand\nnewline-0123"]
    script = (f"const MIN_SECRET_CHARS = {MIN_SECRET_CHARS};\n{function[0]}\n"
              "process.stdout.write(JSON.stringify(JSON.parse(process.argv[1]).map(secretForms)));")
    done = subprocess.run([pi_harness.node, "--input-type=module", "-e", script,
                           json.dumps(keys)], capture_output=True, text=True, timeout=30,
                          env={}, check=True)
    assert [set(forms) for forms in json.loads(done.stdout)] == [secret_forms(k) for k in keys]
