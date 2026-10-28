"""Built-in supply-chain example: SKUs, inventory, suppliers and purchase orders.

Item categories live in their own plugins (``spare_parts``, ``packaging``) and extend ``sku``
the same way a drop-in category does (see ``examples/plugins/cold_chain.py``).
"""

from twotrack.core import Plugin

from .entities import ENTITIES
from .tools import PRECONDITIONS, TOOLS
from .workflow import REORDER_PLAN

PLUGIN = Plugin(
    name="supply_chain",
    version="0.1.0",
    description="Inventory, low stock, reorder suggestions, purchase orders and suppliers.",
    entities=ENTITIES,
    tools=TOOLS,
    workflows=(REORDER_PLAN,),
    preconditions=PRECONDITIONS,
)

__all__ = ["PLUGIN"]
