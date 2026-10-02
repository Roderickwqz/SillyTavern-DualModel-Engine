---
name: codegraph
description: Code intelligence, graph queries, symbol search, callers, callees, and impact analysis via CodeGraph CLI and MCP server.
---

# CodeGraph Skill

This skill provides code intelligence and structural analysis using CodeGraph for Gemini and Antigravity agents.

## Features & Capabilities

- **Symbol Search**: Search function, class, and variable definitions across the project.
- **Call Graph Tracing**: Identify callers and callees for any symbol to understand call paths.
- **Impact Analysis**: Determine upstream and downstream code dependencies before making refactoring changes.
- **Affected Test Discovery**: Locate test files affected by source file modifications.

## CLI Usage

- `codegraph query <symbol>` — Find declarations and occurrences of a symbol.
- `codegraph callers <symbol>` — List all functions/methods calling the symbol.
- `codegraph callees <symbol>` — List all functions/methods called by the symbol.
- `codegraph impact <symbol>` — Perform impact analysis for changes to the symbol.
- `codegraph affected [files...]` — Find affected test files for changed source files.
- `codegraph status` — Show database status and node/edge count.
- `codegraph sync` — Synchronize index with recent changes.

## MCP Server Integration

CodeGraph is configured as an MCP server for Gemini in `~/.gemini/settings.json`:
```json
{
  "mcpServers": {
    "codegraph": {
      "type": "stdio",
      "command": "codegraph",
      "args": ["serve", "--mcp"]
    }
  }
}
```
