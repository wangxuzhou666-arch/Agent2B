"""Canonical JSON, digests and restricted-value masking shared by grants, audit and import."""

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from .contract import Sensitivity

MASK = "***"


def canonical_json(value: Any) -> str:
    """Key-sorted, compact JSON. Raises TypeError/ValueError for non-JSON values."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def sha256_hex(data: str | bytes) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def request_digest(user_id: str, target: str, params: Any) -> str:
    """The digest a confirmation grant is bound to: who, what, and with which params."""
    return sha256_hex(canonical_json({"user": user_id, "target": target, "params": params}))


def jsonable(value: Any) -> Any:
    """Best-effort JSON copy of arbitrary input (unknown objects become strings)."""
    return json.loads(json.dumps(value, default=str))


def restricted_field_ids(registry: Any) -> frozenset[str]:
    """Ids of every restricted field across all entities (core fields and attrs)."""
    return frozenset(
        f.id
        for entity in registry.entities.values()
        for f in entity.all_fields
        if f.sensitivity is Sensitivity.RESTRICTED
    )


def mask_restricted(value: Any, restricted: Iterable[str]) -> Any:
    """Copy of ``value`` with the value of every key named like a restricted field masked."""
    names = frozenset(restricted)
    if isinstance(value, dict):
        return {k: MASK if k in names else mask_restricted(v, names) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_restricted(v, names) for v in value]
    return value
