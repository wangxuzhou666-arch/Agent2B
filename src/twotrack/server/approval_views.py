"""How a pending confirmation is shown to the person looking at it.

A confirmation is asked for once, in the language of whoever asked, but it is read later, maybe
by someone whose console is set to the other language. So its title and summary are worded again
for the reader from what the request holds (the target and its params, or the import's entity,
file and row count), and the time it has left is measured on the server's clock: a browser
whose clock is off would otherwise hide a confirmation that is still valid.
"""

from datetime import datetime

from twotrack.core.entities import import_label, import_summary
from twotrack.core.i18n import Language
from twotrack.core.params import registry_title
from twotrack.runtime import Runtime

from .approvals import PendingRequest
from .schemas import ApprovalRef


def seconds_left(expires_at: datetime, now: datetime) -> float:
    """Seconds from ``now`` until ``expires_at`` (to the millisecond), never below 0."""
    return max(0.0, round((expires_at - now).total_seconds(), 3))


def approval_label(runtime: Runtime, pending: PendingRequest, language: Language) -> str:
    """What is confirmed, in ``language``: the tool's title ("Create purchase order"), or the
    import ("Import Inventory")."""
    if pending.kind == "import":
        entity = runtime.registry.entities.get(pending.entity_id or "")
        if entity is not None:
            return import_label(entity, language)
    return registry_title(runtime.registry, pending.target, language)


def approval_summary(runtime: Runtime, pending: PendingRequest, language: Language) -> str:
    """The confirmation's summary in ``language``, worded again from the request. The text the
    asker saw is kept when that is not possible: the plugin or entity was removed since, or the
    request was made before an import kept its row count."""
    if pending.kind == "import":
        entity = runtime.registry.entities.get(pending.entity_id or "")
        count = pending.params.get("valid_count")
        if entity is None or not isinstance(count, int):
            return pending.summary
        return import_summary(entity, count, pending.filename or "upload", language)
    worded = runtime.pipeline.summarize(pending.target, pending.params, language)
    return pending.summary if worded is None else worded


def approval_ref(runtime: Runtime, *, grant_id: str, target: str | None, summary: str,
                 label: str, expires_at: datetime) -> ApprovalRef:
    """The public handle of a pending confirmation, with the time left as of now. ``target``
    is the id of what it confirms, None only when nothing can name it."""
    return ApprovalRef(grant_id=grant_id, target=target, summary=summary, label=label,
                       expires_at=expires_at,
                       expires_in_s=seconds_left(expires_at, runtime.clock()))


def pending_ref(runtime: Runtime, pending: PendingRequest, language: Language) -> ApprovalRef:
    """``approval_ref`` of a stored request, its label and summary worded for ``language``."""
    return approval_ref(runtime, grant_id=pending.grant_id, target=pending.target,
                        summary=approval_summary(runtime, pending, language),
                        label=approval_label(runtime, pending, language),
                        expires_at=pending.expires_at)
