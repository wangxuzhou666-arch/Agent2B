"""The example CSVs import cleanly: alias headers (one of them Chinese) map, every row is valid."""

from pathlib import Path

import pytest
from world import OPERATOR, build_world

from twotrack.core.entities import ImportService

DATA = Path(__file__).resolve().parents[2] / "examples" / "data"


@pytest.mark.parametrize(("filename", "entity", "chinese_header"), [
    ("spare_parts_sample.csv", "spare_part", "适用机型"),
    ("packaging_sample.csv", "packaging", "物料编码"),
])
def test_sample_csv_previews_with_zero_errors_then_commits(filename, entity, chinese_header):
    world = build_world()
    service = ImportService(world.registry, world.store, world.audit, world.grants)
    raw = (DATA / filename).read_bytes()

    preview = service.preview(OPERATOR, entity, filename, raw)
    assert preview.error_count == 0, [r.errors for r in preview.rows if r.errors]
    assert preview.valid_count == 20
    assert preview.unmapped == []
    assert chinese_header in {m.header for m in preview.mapping}
    assert all(m.header != m.field_id for m in preview.mapping)  # every header is an alias
    assert {r.action for r in preview.rows} == {"new"}

    result = service.commit(OPERATOR, entity, filename, raw, expected_digest=preview.digest,
                            grant_token=preview.grant.token)
    assert result.written == 20 and result.skipped == 0
    assert world.store.count(entity) == 20
    row = world.store.list(entity, limit=1).rows[0]
    assert row["_entity"] == entity
    attr = {"spare_part": "machine_model", "packaging": "material"}[entity]
    assert row[attr]
