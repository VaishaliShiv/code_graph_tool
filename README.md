# code-graph-kit

A local knowledge graph for AI-assisted code review. Parses any codebase into a semantic graph so AI assistants read only what matters — saving up to **12x tokens** on large codebases.

Works with **Claude Code**, **Cursor**, and **Windsurf** via MCP. Fully offline — no API keys, no cloud.

---

## What It Does

- Parses your codebase into a graph of functions, classes, and their relationships
- Semantic search using plain English ("find authentication logic")
- Blast radius — change one file, instantly know everything that breaks
- Live dashboard with D3.js graph visualization
- Token-efficient context for AI assistants via MCP

---

## Requirements

- Python 3.10 or higher
- pip

---

## Installation

### Step 1 — Clone the repo

```bash
git clone https://github.com/yourname/code-graph-kit.git
cd code-graph-kit
```

### Step 2 — Install

```bash
# Full install (recommended) — includes semantic search, file watcher, multi-language AST
pip install -e ".[all]"
```

Or install only what you need:

```bash
pip install -e "."                    # base only (no semantic search)
pip install -e ".[embeddings]"        # + semantic search
pip install -e ".[languages]"         # + multi-language AST parsing
```

### Step 3 — Build the graph for your project

```bash
code-graph-kit build --path /path/to/your/project
```

First run downloads the embedding model (~80MB, cached after that).

---

## Usage

### CLI Commands

```bash
# Build graph
code-graph-kit build --path /your/project

# Search (keyword or natural language)
code-graph-kit search "authentication logic"
code-graph-kit search "verify_token"

# Blast radius — what breaks if this file changes?
code-graph-kit blast --file auth/service.py

# Open live dashboard
code-graph-kit serve
# → Open http://localhost:5000

# Graph statistics
code-graph-kit stats

# Watch for file changes (incremental re-index)
code-graph-kit watch --path /your/project
```

### Try with the sample project

```bash
code-graph-kit build --path sample_project
code-graph-kit serve
```

Open `http://localhost:5000` to explore the graph.

---

## MCP Integration (Claude Code / Cursor / Windsurf)

MCP lets AI assistants query your code graph directly during conversations.

### Step 1 — Edit `mcp.json`

Open `mcp.json` and set `PROJECT_PATH` to your project:

```json
{
  "mcpServers": {
    "code-graph-kit": {
      "command": "code-graph-kit",
      "args": ["mcp"],
      "env": {
        "PROJECT_PATH": "/absolute/path/to/your/project"
      }
    }
  }
}
```

### Step 2 — Add to your AI tool

**Claude Code** — copy `mcp.json` to your project root, or add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "code-graph-kit": {
      "command": "code-graph-kit",
      "args": ["mcp"],
      "env": {
        "PROJECT_PATH": "/absolute/path/to/your/project"
      }
    }
  }
}
```

**Cursor** — add to `.cursor/mcp.json` in your project root.

**Windsurf** — add to `.windsurf/mcp.json` in your project root.

### Step 3 — Build the graph first

```bash
code-graph-kit build --path /absolute/path/to/your/project
```

### MCP Tools Available

| Tool | What It Does |
|---|---|
| `build_graph` | Parse codebase, build the graph |
| `semantic_search` | Search by keyword or plain English |
| `query_graph` | Find callers, callees, tests of a function |
| `get_blast_radius` | Impact analysis for a changed file |
| `get_review_context` | Minimal context needed to review a file |
| `get_stats` | Total nodes, edges, files, languages |

---

## Supported Languages

Python, JavaScript, TypeScript, Go, Rust, Java — via Tree-sitter AST parser.

Adding a new language = one config entry in `src/language_configs.py`.

---

## Architecture

```
src/
├── parser.py           # Tree-sitter universal parser
├── graph_db.py         # SQLite graph store (WAL mode)
├── embeddings.py       # sentence-transformers (all-MiniLM-L6-v2, 384-dim)
├── blast_radius.py     # Impact analysis via graph traversal
├── watcher.py          # File watcher for incremental re-indexing
├── mcp_server.py       # MCP stdio server
├── cli.py              # CLI entry point
└── frontend/
    └── index.html      # D3.js dashboard

sample_project/         # Demo codebase to try the tool
mcp.json                # MCP config template (edit PROJECT_PATH)
```

---

## Token Savings (Measured)

| Query Type | Without Kit | With Kit | Saving |
|---|---|---|---|
| Caller lookup | ~3,800 tokens | ~300 tokens | 12.7x |
| Blast radius | ~3,800 tokens | ~600 tokens | 6.3x |
| Auth search | ~1,663 tokens | ~400 tokens | 4.2x |
| Review context | ~3,809 tokens | ~800 tokens | 1.5x |

---

## Notes

- Embedding model downloads on first run (~80MB), then cached offline
- Graph stored in `.code-review-graph/graph.db` inside your project folder
- File watcher tracks by hash — no git hooks needed
- Dashboard auto-refreshes every 5 seconds
