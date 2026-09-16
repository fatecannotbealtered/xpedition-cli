# MCP transport

Run `xpedition-cli agent serve --transport mcp` as a subprocess. It uses the
MCP 2025-11-25 stdio shape (with negotiation for older supported versions): one
UTF-8 JSON-RPC message per line, logs only on stderr, and no summary line after
EOF. The server handles `initialize`, `ping`, `tools/list`, and read-only
`tools/call` requests.

The exposed tools are:

- `xpedition_snapshot`
- `xpedition_query`
- `xpedition_review`
- `xpedition_capabilities`

Structured tool results include the normalized data and a serialized JSON text
content item. Project and review values remain `_untrusted` data. No MCP tool
performs a write; ChangeSet writes stay on the CLI `dry-run → confirm` path.

Protocol references: [MCP stdio transports](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports) and [MCP tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools).
