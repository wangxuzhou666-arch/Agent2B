"""Entity descriptions and records, projected for the caller's role."""

import pytest

RESTRICTED = {"unit_cost", "supplier"}


def field_ids(fields):
    return [f["id"] for f in fields]


def item_entity(api, user):
    items = api.get("/api/entities", user).json()["items"]
    return next(e for e in items if e["id"] == "item")


def test_entity_listing_hides_restricted_fields_from_viewers_only(api):
    viewer = item_entity(api, "vera")
    admin = item_entity(api, "ada")
    operator = item_entity(api, "olga")
    assert not RESTRICTED & set(field_ids(viewer["fields"]))
    assert RESTRICTED <= set(field_ids(admin["fields"]))
    assert field_ids(operator["fields"]) == field_ids(admin["fields"])
    assert viewer["primary_key"] == "sku" and viewer["label_field"] == "name"


def test_an_entity_says_how_many_fields_are_hidden_but_not_which(api):
    viewer, operator, admin = (item_entity(api, u) for u in ("vera", "olga", "ada"))
    assert viewer["hidden_fields"] == len(RESTRICTED)
    assert operator["hidden_fields"] == admin["hidden_fields"] == 0
    body = api.get("/api/entities", "vera").text
    assert not any(f'"{name}"' in body for name in RESTRICTED)  # never the names


def test_categories_are_nested_under_their_root_with_their_attrs(api):
    listing = api.get("/api/entities", "ada").json()["items"]
    assert [e["id"] for e in listing] == ["item", "bin"]  # a category is not a root
    (bolt,) = item_entity(api, "ada")["categories"]
    assert bolt["id"] == "bolt" and bolt["extends"] == "item" and bolt["description"]
    assert field_ids(bolt["attrs"]) == ["thread_pitch", "vendor_ref"]
    (viewer_bolt,) = item_entity(api, "vera")["categories"]
    assert field_ids(viewer_bolt["attrs"]) == ["thread_pitch"]
    thread = bolt["attrs"][0]
    assert thread["type"] == "float" and thread["required"] is True
    status = next(f for f in item_entity(api, "ada")["fields"] if f["id"] == "status")
    assert status["enum_values"] == ["active", "retired"]


def records(api, user, entity="item", **params):
    return api.get(f"/api/entities/{entity}/records", user, params=params)


def test_records_are_paged_and_counted(api):
    first = records(api, "ada", page_size=4, sort="sku").json()
    second = records(api, "ada", page_size=4, page=2, sort="sku").json()
    assert (first["total"], first["page"], first["page_size"]) == (6, 1, 4)
    assert [r["sku"] for r in first["items"]] == ["A-1", "A-2", "A-3", "A-4"]
    assert [r["sku"] for r in second["items"]] == ["A-5", "B-1"]
    assert records(api, "ada", page=0).status_code == 422
    assert records(api, "ada", page_size=201).status_code == 422


def test_records_rows_are_projected_per_role(api):
    viewer = records(api, "vera", sort="sku").json()["items"]
    admin = records(api, "ada", sort="sku").json()["items"]
    assert not RESTRICTED & set(viewer[0]) and RESTRICTED <= set(admin[0])
    assert "Supplier" not in str(viewer) and "0.25" not in str(viewer)
    bolt_viewer = next(r for r in viewer if r["sku"] == "B-1")
    bolt_admin = next(r for r in admin if r["sku"] == "B-1")
    assert bolt_viewer["_entity"] == "bolt" and bolt_viewer["thread_pitch"] == 1.25
    assert "vendor_ref" not in bolt_viewer and bolt_admin["vendor_ref"] == "VR-77"


def test_sort_and_filters(api):
    desc = [r["sku"] for r in records(api, "vera", sort="-qty", page_size=3).json()["items"]]
    assert desc == ["A-3", "A-5", "A-1"]
    retired = records(api, "vera", status="retired").json()
    assert retired["total"] == 1 and retired["items"][0]["sku"] == "A-3"
    either = api.get("/api/entities/item/records?sku=A-1&sku=A-2&sort=sku", "vera").json()
    assert [r["sku"] for r in either["items"]] == ["A-1", "A-2"]
    assert records(api, "vera", qty=40).json()["total"] == 1  # numeric filter coerced


def test_category_listing_returns_only_its_own_rows(api):
    page = records(api, "ada", entity="bolt").json()
    assert page["total"] == 1 and page["items"][0]["sku"] == "B-1"
    assert records(api, "ada", entity="bolt", thread_pitch="1.25").json()["total"] == 1


def test_filtering_or_sorting_by_a_hidden_field_is_refused(api):
    for params in ({"supplier": "Supplier Alpha"}, {"sort": "unit_cost"}, {"sort": "-supplier"}):
        response = records(api, "vera", **params)
        assert response.status_code == 400, params
        assert response.json()["error"]["code"] == "unknown_field"
    assert records(api, "ada", supplier="Supplier Alpha").json()["total"] == 3
    assert records(api, "olga", sort="-unit_cost").status_code == 200


def test_bad_filter_values_and_unknown_things(api):
    bad = records(api, "ada", status="nonsense")
    assert bad.status_code == 400 and "status" in bad.json()["error"]["message"]
    assert records(api, "ada", qty="many").status_code == 400
    assert records(api, "ada", nonexistent="x").json()["error"]["code"] == "unknown_field"
    assert records(api, "ada", entity="nope").status_code == 404


def test_single_record(api):
    viewer = api.get("/api/entities/item/records/A-1", "vera").json()
    admin = api.get("/api/entities/item/records/A-1", "ada").json()
    assert viewer["name"] == "Hex bolt" and not RESTRICTED & set(viewer)
    assert admin["unit_cost"] == 0.25 and admin["_entity"] == "item"
    assert api.get("/api/entities/item/records/ZZZ", "ada").status_code == 404
    assert api.get("/api/entities/bolt/records/A-1", "ada").status_code == 404  # wrong category
    assert api.get("/api/entities/bolt/records/B-1", "ada").json()["thread_pitch"] == 1.25
    assert api.get("/api/entities/nope/records/A-1", "ada").status_code == 404


def test_entities_need_a_token(api):
    for path in ("/api/entities", "/api/entities/item/records", "/api/entities/item/records/A-1"):
        assert api.get(path).status_code == 401


@pytest.mark.parametrize("pk", ["A-1' OR '1'='1", "A-1; DROP TABLE e_item", "%27"])
def test_primary_keys_are_never_interpolated(api, pk):
    assert api.get(f"/api/entities/item/records/{pk}", "ada").status_code == 404
    assert api.runtime.store.count("item") == 6
