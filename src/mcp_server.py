"""
MCP Server for Code Graph Kit.

Exposes graph tools via the Model Context Protocol so AI assistants
(Claude Code, Cursor, Windsurf, etc.) can query the code graph.

When an AI tool has this MCP server configured, it can:
  - Get blast radius for changed files
  - Search code by meaning (semantic search)
  - Get minimal review context (token-optimized)
  - Query callers, callees, tests, dependencies

Usage:
    python mcp_server.py --project /path/to/your/project

Or configure in .mcp.json / claude_desktop_config.json.
"""

import sys
import os
import json
import time
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))

try:
    from src.parser import UniversalParser
    from src.graph_db import GraphDB
    from src.embeddings import EmbeddingEngine, hybrid_search, detect_query_mode, CrossEncoderReranker
    from src.blast_radius import BlastRadiusAnalyzer
except ImportError:
    sys.path.insert(0, _HERE)
    from parser import UniversalParser
    from graph_db import GraphDB
    from embeddings import EmbeddingEngine, hybrid_search, detect_query_mode, CrossEncoderReranker
    from blast_radius import BlastRadiusAnalyzer


class MCPServer:
    """
    Minimal MCP server using stdio transport.
    
    Reads JSON-RPC messages from stdin, dispatches to tool handlers,
    writes responses to stdout. This is the standard MCP protocol
    that Claude Code, Cursor, and others understand.
    """
    
    def __init__(self, project_path: str):
        self.project_path = os.path.abspath(project_path)
        self.db_path = os.path.join(self.project_path, ".code-review-graph", "graph.db")
        self.db = GraphDB(self.db_path)
        self.engine = EmbeddingEngine()
        self.reranker = CrossEncoderReranker()   # no-op if not installed
        self.analyzer = BlastRadiusAnalyzer(self.db)
        
        self._TOKENS_PER_FILE = 800
        self._TIME_PER_FILE_S = 2.0

        # Tool registry
        self.tools = {
            "build_graph": {
                "description": "Build or rebuild the code graph for this project. Run this first before using other tools.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
                "handler": self._build_graph,
            },
            "get_blast_radius": {
                "description": "Get the blast radius (impact analysis) for a changed file. Shows all affected functions, files, tests, and risk level.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string", "description": "Relative path to the changed file"},
                    },
                    "required": ["file"],
                },
                "handler": self._get_blast_radius,
            },
            "get_review_context": {
                "description": "Get token-optimized review context for changed files. Returns only the minimal set of information the AI needs to review the change.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string", "description": "Relative path to the changed file"},
                    },
                    "required": ["file"],
                },
                "handler": self._get_review_context,
            },
            "semantic_search": {
                "description": "Search code entities by name or meaning. Results include 1-hop graph context (callers, callees, tests) so no follow-up query_graph needed. Mode auto-detects: snake_case/PascalCase queries → keyword, natural language → hybrid.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Code identifier (login_user) or natural language (authentication logic)"},
                        "mode": {
                            "type": "string",
                            "enum": ["auto", "hybrid", "keyword", "semantic"],
                            "description": "auto (default) detects best mode; override with hybrid/keyword/semantic",
                            "default": "auto",
                        },
                        "limit": {"type": "integer", "description": "Max results (default 5)", "default": 5},
                    },
                    "required": ["query"],
                },
                "handler": self._semantic_search,
            },
            "query_graph": {
                "description": "Query the code graph for relationships. Find callers of a function, what it calls, its tests, or file dependencies.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "function_name": {"type": "string", "description": "Name of the function to query"},
                        "query_type": {
                            "type": "string",
                            "enum": ["callers", "callees", "tests", "all"],
                            "description": "What to look up",
                            "default": "all",
                        },
                    },
                    "required": ["function_name"],
                },
                "handler": self._query_graph,
            },
            "get_stats": {
                "description": "Get graph statistics: total nodes, edges, files, languages, and embedding count.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
                "handler": self._get_stats,
            },
        }
    
    # ─── Activity logging helper ───────────────────────

    def _log(self, op, target, time_ms, files_with, extra=None):
        """Write to the shared activity_log table in SQLite."""
        try:
            stats = self.db.get_stats()
            total = stats.get("total_files", 0)
            tpf = self._TOKENS_PER_FILE
            tokens_with    = files_with * tpf + 400
            tokens_without = max(total * tpf, 1)
            reduction      = tokens_without / tokens_with
            time_without_s = total * self._TIME_PER_FILE_S
            self.db.log_activity(
                op=op, target=target, time_ms=time_ms,
                files_with=files_with, files_without=total,
                tokens_with=tokens_with, tokens_without=tokens_without,
                reduction=reduction, time_without_s=time_without_s,
                source="claude", extra=extra or {},
            )
        except Exception:
            pass   # never crash Claude on a logging error

    # ─── Tool handlers ─────────────────────────────────
    
    def _build_graph(self, params: dict) -> dict:
        parser = UniversalParser()
        nodes, edges = parser.parse_project(self.project_path)
        
        self.db = GraphDB(self.db_path)
        self.db.init()
        self.db.upsert_nodes(nodes)
        self.db.upsert_edges(edges)
        self.engine.embed_nodes(self.db)
        self.analyzer = BlastRadiusAnalyzer(self.db)
        
        stats = self.db.get_stats()
        return {
            "status": "success",
            "message": f"Built graph: {stats['total_nodes']} nodes, {stats['total_edges']} edges, {stats['total_files']} files",
            "stats": stats,
        }
    
    def _get_blast_radius(self, params: dict) -> dict:
        filepath = params["file"]
        t0 = time.time()
        result = self.analyzer.analyze_file(filepath)
        self._log(
            op="blast", target=filepath,
            time_ms=(time.time() - t0) * 1000,
            files_with=result.get("affected_file_count", 1),
            extra={"risk_level": result.get("risk_level"), "risk_score": result.get("risk_score")},
        )
        return result

    def _get_review_context(self, params: dict) -> dict:
        """Token-optimized review context — the compact payload AI actually uses."""
        filepath = params["file"]
        t0 = time.time()
        blast = self.analyzer.analyze_file(filepath)
        stats = self.db.get_stats()
        total_files = stats["total_files"]

        # Token savings: compare files AI must read WITH vs WITHOUT the kit.
        # "Without" = entire codebase. "With" = only the focused review_order list.
        # Use review_order length (focused set), not affected_file_count (full blast),
        # because review_order is what the AI actually reads.
        files_to_read_with_kit = max(len(blast["review_order"]), 1)
        tokens_per_file = 800
        tokens_without = total_files * tokens_per_file
        tokens_with = files_to_read_with_kit * tokens_per_file
        reduction = round(tokens_without / max(tokens_with, 1), 1)
        token_savings = f"{reduction}x"

        self._log(
            op="review_context", target=filepath,
            time_ms=(time.time() - t0) * 1000,
            files_with=files_to_read_with_kit,
        )

        # Build compact review context
        context = {
            "changed_file": filepath,
            "risk_level": blast["risk_level"],
            "risk_score": blast["risk_score"],
            "token_savings": token_savings,
            "files_reviewed_with_kit": files_to_read_with_kit,
            "total_project_files": total_files,
            "files_to_review": blast["review_order"],
            "affected_functions": [
                {
                    "function": a["function"],
                    "file": a["file"],
                    "risk": a["risk"],
                    "reason": a["reason"],
                }
                for a in blast["directly_affected"]
            ],
            "test_coverage": {
                "covered": [t["covers"] for t in blast["tests"]["covered"]],
                "missing": blast["tests"]["missing"],
                "warning": f"{len(blast['tests']['missing'])} functions have NO tests" if blast["tests"]["missing"] else None,
            },
            "indirect_impact_count": len(blast["indirectly_affected"]),
        }
        return context
    
    def _semantic_search(self, params: dict) -> dict:
        query = params["query"]
        limit = params.get("limit", 5)
        t0 = time.time()

        # Auto-detect mode if not explicitly set
        requested_mode = params.get("mode", "auto")
        if requested_mode == "auto":
            mode = detect_query_mode(query)
        else:
            mode = requested_mode

        # Retrieve more candidates than needed — reranker will cut to `limit`.
        # If reranker unavailable, retrieval_k == limit (no wasted work).
        retrieval_k = limit * 4 if self.reranker._available else limit

        if mode == "keyword":
            raw = self.db.keyword_search(query, limit=retrieval_k)
            candidates = []
            for r in raw:
                doc = r.get("docstring") or ""
                candidates.append({
                    "name": r["name"], "type": r["type"],
                    "file": r["file"], "line": r.get("line", 0),
                    "signature": r.get("signature") or "",
                    "docstring": doc[:120].rstrip() + "…" if len(doc) > 120 else doc,
                    "score": 1.0,
                })
        elif mode == "semantic":
            raw = self.engine.semantic_search(self.db, query, top_k=retrieval_k)
            candidates = []
            for r in raw:
                doc = r.get("docstring") or ""
                candidates.append({
                    "name": r["name"], "type": r["type"],
                    "file": r["file"], "line": r.get("line", 0),
                    "signature": r.get("signature") or "",
                    "docstring": doc[:120].rstrip() + "…" if len(doc) > 120 else doc,
                    "score": r.get("similarity", 0),
                })
        else:
            candidates = hybrid_search(self.db, self.engine, query, top_k=retrieval_k)

        # Cross-encoder re-rank: scores (query, document) jointly for
        # much higher precision than bi-encoder cosine similarity alone.
        results = self.reranker.rerank(query, candidates, top_k=limit)

        # Graph-augment: attach 1-hop context inline so AI needs no
        # follow-up query_graph call.
        for r in results:
            ctx = self.db.get_graph_context(r["name"])
            if ctx["callers"] or ctx["callees"] or ctx["tests"]:
                r["graph"] = {
                    "callers": [c["name"] for c in ctx["callers"]],
                    "calls": ctx["callees"],
                    "tested_by": ctx["tests"],
                }

        result_files = len({r.get("file", "") for r in results if r.get("file")})
        self._log(
            op=f"search:{mode}", target=query,
            time_ms=(time.time() - t0) * 1000,
            files_with=result_files,
            extra={
                "result_count": len(results),
                "mode": mode,
                "auto": requested_mode == "auto",
                "reranked": self.reranker._available,
            },
        )
        return {"query": query, "mode": mode, "reranked": self.reranker._available, "results": results}

    def _query_graph(self, params: dict) -> dict:
        func_name = params["function_name"]
        query_type = params.get("query_type", "all")
        t0 = time.time()

        result = {"function": func_name}

        if query_type in ("callers", "all"):
            result["callers"] = self.db.get_callers(func_name)
        if query_type in ("callees", "all"):
            result["callees"] = self.db.get_callees(func_name)
        if query_type in ("tests", "all"):
            result["tests"] = self.db.get_tests_for(func_name)

        files_touched = 1 + len({
            c.get("file","") for c in
            result.get("callers",[]) + result.get("callees",[])
            if c.get("file")
        })
        self._log(
            op=f"query_graph:{query_type}", target=func_name,
            time_ms=(time.time() - t0) * 1000,
            files_with=files_touched,
        )
        return result
    
    def _get_stats(self, params: dict) -> dict:
        return self.db.get_stats()
    
    # ─── MCP Protocol (JSON-RPC over stdio) ────────────
    
    def run(self):
        """Main loop: read JSON-RPC from stdin, write responses to stdout."""
        sys.stderr.write(f"[mcp] Code Graph Kit server started for: {self.project_path}\n")
        sys.stderr.write(f"[mcp] {len(self.tools)} tools available\n")
        sys.stderr.flush()
        
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                continue
            
            method = request.get("method", "")
            req_id = request.get("id")
            params = request.get("params", {})
            
            response = None
            
            if method == "initialize":
                response = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {"listChanged": False},
                    },
                    "serverInfo": {
                        "name": "code-graph-kit",
                        "version": "1.0.0",
                    },
                }
            
            elif method == "tools/list":
                tool_list = []
                for name, tool in self.tools.items():
                    tool_list.append({
                        "name": name,
                        "description": tool["description"],
                        "inputSchema": tool["inputSchema"],
                    })
                response = {"tools": tool_list}
            
            elif method == "tools/call":
                tool_name = params.get("name", "")
                tool_args = params.get("arguments", {})
                
                if tool_name in self.tools:
                    try:
                        result = self.tools[tool_name]["handler"](tool_args)
                        response = {
                            "content": [{
                                "type": "text",
                                "text": json.dumps(result, indent=2, default=str),
                            }],
                        }
                    except Exception as e:
                        response = {
                            "content": [{
                                "type": "text",
                                "text": json.dumps({"error": str(e)}),
                            }],
                            "isError": True,
                        }
                else:
                    response = {
                        "content": [{
                            "type": "text",
                            "text": json.dumps({"error": f"Unknown tool: {tool_name}"}),
                        }],
                        "isError": True,
                    }
            
            elif method == "notifications/initialized":
                continue  # No response needed
            
            if response is not None and req_id is not None:
                json_response = json.dumps({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": response,
                })
                sys.stdout.write(json_response + "\n")
                sys.stdout.flush()


def main():
    argparser = argparse.ArgumentParser(description="Code Graph Kit MCP Server")
    argparser.add_argument("--project", default=".", help="Project root directory")
    args = argparser.parse_args()
    
    server = MCPServer(args.project)
    server.run()


if __name__ == "__main__":
    main()
