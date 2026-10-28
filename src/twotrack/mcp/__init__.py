"""MCP export: run ``twotrack mcp`` to offer the registry's tools to an MCP client.

See ``twotrack.mcp.server`` for the identity, rows and write-approval rules, and docs/MCP.md
for client configuration.
"""

from .server import (
    ASK,
    Identity,
    IdentityError,
    McpOptions,
    TwotrackMcp,
    build_server,
    resolve_identity,
    serve_stdio,
)

__all__ = [
    "ASK",
    "Identity",
    "IdentityError",
    "McpOptions",
    "TwotrackMcp",
    "build_server",
    "resolve_identity",
    "serve_stdio",
]
