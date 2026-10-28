# Add an item category

A category is a kind of item with fields of its own: spare parts have a machine model, cold-chain
items have a shelf life. In twotrack a category is one Python file. This tutorial adds a
`hazmat_item` category with three attributes, restarts the server, and shows the category in the
Data window's API, in the import, and in a question that routes to a built-in tool. No core file
changes and no migration ([ADR 0006](decisions/0006-categories-extend-a-root-with-attrs.md)).

The model to read first is [`examples/plugins/cold_chain.py`](../examples/plugins/cold_chain.py).
This tutorial is the smaller version of it: a category without a report of its own.

Every command and output below was run against a fresh checkout and a fresh data directory. The
console reads the same endpoints (`GET /api/entities`, the import preview and commit), so these
calls show what its Data window and import wizard show; the tutorial was not clicked through in a
browser. The API runs on port 8766 here; use any free port.

## 1. Start the API and look at the categories

```sh
export TWOTRACK_DEMO_MODE=1 TWOTRACK_PROVIDER=mock TWOTRACK_DATA_DIR=./data
uv run twotrack serve --port 8766 &
TOKEN=$(curl -s -X POST localhost:8766/api/auth/demo -H 'content-type: application/json' \
  -d '{"role":"operator"}' | jq -r .access_token)
curl -s localhost:8766/api/entities -H "authorization: Bearer $TOKEN" \
  | jq -c '.items[] | select(.id=="sku") | {root: .id, categories: [.categories[].id]}'
```

```
{"root":"sku","categories":["packaging","spare_part"]}
```

Demo mode signs in without a password and seeds synthetic data, which is fine for a tutorial and
for nothing else ([SECURITY.md](../SECURITY.md)). Stop the server with
`pkill -f 'twotrack serve --port 8766'`.

## 2. Write the plugin

Create `my_plugins/hazmat.py`. A plugin is a module that exports `PLUGIN`; the category is an
`EntitySpec` that `extends="sku"` and declares only `attrs`.

```python
from twotrack.core import EntitySpec, FieldSpec, FieldType, Plugin

HAZARD_CLASSES = ("flammable", "corrosive", "toxic", "oxidizer")

HAZMAT_ITEM = EntitySpec(
    id="hazmat_item",
    label={"en": "Hazmat Item", "zh": "危险品物料"},
    description={"en": "An item that needs hazardous-goods handling.",
                 "zh": "需要按危险品处理的物料。"},
    extends="sku",
    synonyms={"en": ("hazmat", "hazardous goods"), "zh": ("危险品",)},
    attrs=(
        FieldSpec(id="un_number", label={"en": "UN Number", "zh": "UN 编号"}, required=True,
                  description={"en": "Four-digit UN number of the substance.",
                               "zh": "物质的四位 UN 编号。"},
                  aliases=("un no", "UN编号")),
        FieldSpec(id="hazard_class", label={"en": "Hazard Class", "zh": "危险类别"},
                  description={"en": "Main hazard of the item.", "zh": "物料的主要危险性。"},
                  type=FieldType.ENUM, enum_values=HAZARD_CLASSES, aliases=("hazard",)),
        FieldSpec(id="flash_point_c", label={"en": "Flash Point (C)", "zh": "闪点"},
                  description={"en": "Lowest temperature at which vapour can ignite, in "
                                     "degrees Celsius.",
                               "zh": "蒸气可被点燃的最低温度，单位摄氏度。"},
                  type=FieldType.FLOAT, unit="C", aliases=("flash point",)),
    ),
)

PLUGIN = Plugin(name="hazmat", version="0.1.0", entities=(HAZMAT_ITEM,),
                description="Hazmat item category.")
```

What each part does:

- `extends="sku"`: the rows live in the SKU table and inherit its fields (`sku_id`, `name`,
  `unit_cost`, `reorder_point`, ...). The three attrs are stored as JSON in a column that already
  exists, so the table does not change.
- `label`, `description` and `synonyms`: a plain string is used for every language, a
  `{"en": ..., "zh": ...}` table per language. English is required whenever a table is given.
  The label is how the console names the category; the label, the id and the synonyms are how a
  question names it.
- `aliases`: extra column headers the import accepts. The id and the label in every language
  already map; "UN Number" needs no alias.
- `required=True` on `un_number`: an import row without it is rejected. `ENUM` needs `enum_values`.

## 3. Restart with the plugin path

```sh
TWOTRACK_PLUGIN_PATHS=my_plugins uv run twotrack serve --port 8766 &
```

The registry loads the built-in plugins, then every `*.py` file or package in each directory of
`TWOTRACK_PLUGIN_PATHS` (separate several with `:`), in sorted order. Sign in again with the
command from step 1, and the category is there:

```sh
curl -s localhost:8766/api/entities -H "authorization: Bearer $TOKEN" \
  | jq -c '.items[] | select(.id=="sku") | {root: .id, categories: [.categories[].id]}'
curl -s localhost:8766/api/entities -H "authorization: Bearer $TOKEN" \
  | jq -c '.items[].categories[] | select(.id=="hazmat_item") | {id, label, attrs: [.attrs[] | [.id, .type, .required]]}'
```

```
{"root":"sku","categories":["packaging","spare_part","hazmat_item"]}
{"id":"hazmat_item","label":"Hazmat Item","attrs":[["un_number","str",true],["hazard_class","enum",false],["flash_point_c","float",false]]}
```

The Data window builds its category tabs from this response, so `Hazmat Item` appears as a tab
next to Spare Part and Packaging, with the three attrs as columns. The import wizard lists the
category as a target the same way.

## 4. Import some rows

Save as `hazmat_sample.csv` (the headers are the labels of the SKU fields, then of the attrs):

```csv
SKU ID,Name,Unit,Unit Cost,Avg Daily Demand,Reorder Point,Lead Time (days),Criticality,UN Number,Hazard Class,Flash Point (C)
SKU-30001,Acetone 20L Drum,drum,88.50,0.8,12,14,B,1090,flammable,-20
SKU-30002,Sulfuric Acid 25L Carboy,ea,64.00,0.5,8,21,A,1830,corrosive,
SKU-30003,Hydrogen Peroxide 30% 10L,ea,41.25,0.3,6,10,C,2014,oxidizer,
```

An import is a preview, then a commit that needs the one-time grant the preview issued
([ADR 0007](decisions/0007-deterministic-import.md)):

```sh
PREVIEW=$(curl -s -X POST localhost:8766/api/entities/hazmat_item/import/preview \
  -H "authorization: Bearer $TOKEN" -F file=@hazmat_sample.csv)
echo "$PREVIEW" | jq -c '{valid_count, error_count, unmapped, mapping: [.mapping[] | [.header, .field_id]]}'
```

```
{"valid_count":3,"error_count":0,"unmapped":[],"mapping":[["SKU ID","sku_id"],["Name","name"],["Unit","unit"],["Unit Cost","unit_cost"],["Avg Daily Demand","avg_daily_demand"],["Reorder Point","reorder_point"],["Lead Time (days)","lead_time_days"],["Criticality","criticality"],["UN Number","un_number"],["Hazard Class","hazard_class"],["Flash Point (C)","flash_point_c"]]}
```

Every column mapped through a declared header, and nothing was written yet. Commit with the
preview's digest and grant (the console does this when you press Approve):

```sh
DIGEST=$(echo "$PREVIEW" | jq -r .digest); GRANT=$(echo "$PREVIEW" | jq -r .grant.grant_id)
curl -s -X POST localhost:8766/api/entities/hazmat_item/import/commit \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d "{\"digest\":\"$DIGEST\",\"grant_id\":\"$GRANT\"}" \
  | jq -c '{status, result: {entity: .result.entity, written: .result.written, skipped: .result.skipped}}'
curl -s localhost:8766/api/entities/hazmat_item/records -H "authorization: Bearer $TOKEN" \
  | jq -c '{total, first: .items[0] | {sku_id, name, un_number, hazard_class, flash_point_c}}'
```

```
{"status":"ok","result":{"entity":"hazmat_item","written":3,"skipped":0}}
{"total":3,"first":{"sku_id":"SKU-30001","name":"Acetone 20L Drum","un_number":"1090","hazard_class":"flammable","flash_point_c":-20.0}}
```

## 5. Ask a question that routes to it

The built-in stock and purchase-order tools take a `category` filter for any entity that extends
`sku`. A question that names the category by its label, id or a synonym is bound to that filter
with no code ([ADR 0004](decisions/0004-scope-guard.md)):

```sh
for q in "which hazmat items are low on stock" "哪些危险品物料库存不足"; do
  curl -s -X POST 'localhost:8766/api/query?stream=false' -H "authorization: Bearer $TOKEN" \
    -H 'content-type: application/json' -d "{\"query\":\"$q\"}" \
    | jq -c '{target: .decision.target, source: .decision.source, params: .decision.params, skus: [.rows[].sku_id], aggregates, answer}'
done
```

```
{"target":"low_stock","source":"rule","params":{"category":"hazmat_item"},"skus":["SKU-30002","SKU-30001","SKU-30003"],"aggregates":{"low_stock_count":3,"class_a_count":1,"total_shortfall":26},"answer":"3 rows matched (Category: Hazmat Item). SKUs below reorder point: 3. Class A SKUs among them: 1. Total shortfall (units): 26."}
{"target":"low_stock","source":"rule","params":{"category":"hazmat_item"},"skus":["SKU-30002","SKU-30001","SKU-30003"],"aggregates":{"low_stock_count":3,"class_a_count":1,"total_shortfall":26},"answer":"共 3 条记录（类别：危险品物料）。低于再订货点的 SKU：3。其中 A 类 SKU：1。缺口合计（件）：26。"}
```

`source: rule` means the rule arm routed it; there is no model in this run (`mock` provider).
All three items count as low stock because they have no inventory records yet and a reorder point
above zero. Every number in the answer is an aggregate the tool computed.

A category that needs a report of its own (the cold-chain `expiring_soon`) adds a `Tool` to
`tools=(...)`. Beside its name, description and params, a tool declares who may run it, what it
does, whether a run waits for approval, what it may return and whether exploration may offer it
to a model. None of the five has a default:

```python
EXPIRING_SOON = Tool(
    name="expiring_soon", description={...}, params_model=ExpiringSoonParams,
    handler=expiring_soon,
    roles=("admin", "operator", "viewer"), effect=ResourceEffect.READ,
    approval=Approval.NONE,  # a read: there is nothing to approve
    outputs=ToolOutputs(aggregates=("item_count", "expiring_units", "soonest_days_left"),
                        columns=("sku_id", "sku_name", "warehouse", "on_hand", "storage_temp_c",
                                 "expires_on", "days_left")),
    explorable=True)
```

`approval` is declared so a reviewer sees whether a run stops for a person (a write must say
`required`, a read `none`). `outputs` is declared for data minimization: a result with an aggregate
key or column the tool did not list becomes an audited error and no data. The role/effect table
(`viewer` reads only) lives in core, so installing a plugin cannot widen who may run what; an
in-process plugin is still trusted code, not a sandbox. [ADD_A_TOOL.md](ADD_A_TOOL.md) walks
through each declaration step by step; see also the example plugin and
[ARCHITECTURE.md](ARCHITECTURE.md#the-plugin-model).

## What fails start-up

The registry checks every plugin and reports all problems at once; the server does not start
(`core/registry.py`, `core/contract.py`). Three you can reproduce:

```sh
# attrs written as fields: a category may only add attrs
TWOTRACK_PLUGIN_PATHS=bad1 uv run twotrack serve --port 8766
```
```
error: cannot start: plugin registry is invalid (1 problem(s)):
  - entity 'hazmat_item': extends 'sku' so it may only add attrs; core fields come from the root entity
```

```sh
# the same plugin file twice in one directory
TWOTRACK_PLUGIN_PATHS=bad2 uv run twotrack serve --port 8766
```
```
error: cannot start: plugin registry is invalid (2 problem(s)):
  - duplicate plugin name 'hazmat'
  - duplicate entity name 'hazmat_item': declared by plugin 'hazmat' and plugin 'hazmat'
```

```sh
# the plugin removed while its rows are in the database
TWOTRACK_PLUGIN_PATHS= uv run twotrack serve --port 8766
```
```
error: cannot start: storage schema does not match the registry; migrate the database before starting
  - table 'e_sku' holds rows of undeclared entities ['hazmat_item']
```

The same check also refuses: a plugin file that fails to import or exports no `PLUGIN`; a path in
`TWOTRACK_PLUGIN_PATHS` that is not a directory; an id that does not match `^[a-z][a-z0-9_]*$`;
an unknown parent in `extends` or a cycle; a duplicate field id or an import header that two
fields share; a label with no English text; an enum without `enum_values`; a restricted field
without `visible_roles`; a tool with no roles, an unknown role (known: `admin`, `operator`,
`viewer`), an unknown precondition or a duplicate name. `tests/core/test_registry.py` covers them.

There is no automatic migration, by design: removing a category that already has rows takes a
manual change to the database before the server will start again.
