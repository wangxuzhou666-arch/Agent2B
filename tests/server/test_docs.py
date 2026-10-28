"""Interactive docs exist in demo mode only, with a CSP scoped to the docs page."""

import base64
import hashlib
import re

from tests.server.conftest import Api
from twotrack.server.app import create_app

SCRIPT = re.compile(r"<script>(.*?)</script>", re.DOTALL)


def test_demo_mode_serves_the_docs_page_and_the_live_schema(make_runtime):
    api = Api(make_runtime(demo_mode=True))
    page = api.client.get("/docs")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert "swagger-ui-bundle.js" in page.text and "/openapi.json" in page.text
    policy = page.headers["content-security-policy"]
    inline = SCRIPT.findall(page.text)
    assert len(inline) == 1
    digest = base64.b64encode(hashlib.sha256(inline[0].encode()).digest()).decode()
    assert f"'sha256-{digest}'" in policy  # the page's one inline script, and nothing else
    assert "script-src https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js" \
        in policy and "'unsafe-inline'" not in policy.split("script-src")[1].split(";")[0]
    assert "frame-ancestors 'none'" in policy and "connect-src 'self'" in policy
    assert page.headers["x-frame-options"] == "DENY"

    schema = api.client.get("/openapi.json")
    assert schema.status_code == 200
    assert schema.json() == create_app(None).openapi()  # the same contract as the export
    assert schema.headers["content-security-policy"].startswith("default-src 'none'")


def test_the_docs_policy_does_not_leak_to_other_routes(make_runtime):
    api = Api(make_runtime(demo_mode=True))
    health = api.client.get("/api/health")
    assert health.headers["content-security-policy"] == \
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    assert api.client.get("/redoc").status_code == 404


def test_outside_demo_mode_there_are_no_docs(api):
    for path in ("/docs", "/openapi.json"):
        assert api.client.get(path).status_code == 404, path
