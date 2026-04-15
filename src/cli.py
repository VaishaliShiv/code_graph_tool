"""
CLI and Web Server for Code Graph Kit.

Provides:
  - CLI commands: build, search, blast, serve, stats
  - Web server: serves the dashboard + JSON API

Usage:
    python cli.py build --path ./my-project
    python cli.py search --query "authentication"
    python cli.py blast --file auth/service.py
    python cli.py serve --port 8000
"""

import sys
import os
import json
import time
import datetime
import argparse
import socket
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

_HERE = os.path.dirname(os.path.abspath(__file__))

try:
    # Installed package — use package imports
    from src.parser import UniversalParser
    from src.graph_db import GraphDB
    from src.embeddings import EmbeddingEngine, hybrid_search
    from src.blast_radius import BlastRadiusAnalyzer
    from src.watcher import FileWatcher
except ImportError:
    # Dev / direct invocation — add src dir to path
    sys.path.insert(0, _HERE)
    from parser import UniversalParser
    from graph_db import GraphDB
    from embeddings import EmbeddingEngine, hybrid_search
    from blast_radius import BlastRadiusAnalyzer
    from watcher import FileWatcher


# ─── Core operations ───────────────────────────────────────

def build_graph(project_path: str, db_path: str = None) -> dict:
    """Build the complete graph for a project."""
    if db_path is None:
        db_path = os.path.join(project_path, ".code-review-graph", "graph.db")
    
    print(f"\n[build] Parsing project: {project_path}")
    start = time.time()
    
    parser = UniversalParser()
    nodes, edges = parser.parse_project(project_path)
    
    parse_time = time.time() - start
    print(f"[build] Parsed {len(nodes)} nodes and {len(edges)} edges in {parse_time:.2f}s")
    
    # Store in database
    db = GraphDB(db_path)
    db.init()
    db.upsert_nodes(nodes)
    db.upsert_edges(edges)
    
    # Build embeddings
    print("[build] Building semantic embeddings...")
    engine = EmbeddingEngine()
    engine.embed_nodes(db)
    
    stats = db.get_stats()
    db.close()
    
    total_time = time.time() - start
    print(f"\n[build] Complete in {total_time:.2f}s")
    print(f"  Nodes: {stats['total_nodes']}")
    print(f"  Edges: {stats['total_edges']}")
    print(f"  Files: {stats['total_files']}")
    print(f"  Embeddings: {stats['total_embeddings']}")
    print(f"  Languages: {stats['languages']}")
    
    return stats


def search_graph(project_path: str, query: str, mode: str = "hybrid"):
    """Search the graph for nodes matching a query."""
    db_path = os.path.join(project_path, ".code-review-graph", "graph.db")
    db = GraphDB(db_path)
    engine = EmbeddingEngine()
    
    if mode == "keyword":
        results = db.keyword_search(query)
        print(f"\n[search] Keyword results for '{query}':")
    elif mode == "semantic":
        results = engine.semantic_search(db, query)
        print(f"\n[search] Semantic results for '{query}':")
    else:
        results = hybrid_search(db, engine, query)
        print(f"\n[search] Hybrid results for '{query}':")
    
    for i, r in enumerate(results, 1):
        score = r.get("score", r.get("similarity", ""))
        print(f"  {i}. {r['name']} ({r['type']}) in {r['file']}")
        if score:
            print(f"     Score: {score}")
        if r.get("signature"):
            print(f"     {r['signature']}")
        if r.get("docstring"):
            print(f"     {r['docstring'][:80]}...")
    
    db.close()
    return results


def blast_radius(project_path: str, filepath: str):
    """Compute blast radius for a changed file."""
    db_path = os.path.join(project_path, ".code-review-graph", "graph.db")
    db = GraphDB(db_path)
    analyzer = BlastRadiusAnalyzer(db)
    
    result = analyzer.analyze_file(filepath)
    
    print(f"\n[blast] Blast radius for: {filepath}")
    print(f"  Risk level: {result['risk_level']} (score: {result['risk_score']})")
    print(f"  Changed functions: {', '.join(result['changed_functions'])}")
    print(f"  Affected files: {result['affected_file_count']}")
    
    if result['directly_affected']:
        print(f"\n  Direct impact:")
        for item in result['directly_affected']:
            print(f"    ⚠ {item['function']} in {item['file']} — {item['reason']}")
    
    if result['indirectly_affected']:
        print(f"\n  Indirect impact:")
        for item in result['indirectly_affected']:
            print(f"    → {item['function']} in {item['file']} — {item['reason']}")
    
    print(f"\n  Tests:")
    for t in result['tests']['covered']:
        print(f"    ✓ {t['test_name']} covers {t['covers']}")
    for m in result['tests']['missing']:
        print(f"    ✗ {m} has NO tests!")
    
    print(f"\n  Suggested review order:")
    for step in result['review_order']:
        print(f"    {step}")
    
    db.close()
    return result


# ─── Web Server ────────────────────────────────────────────

class DashboardHandler(SimpleHTTPRequestHandler):
    """HTTP handler for the dashboard API and static files."""

    project_path = "."
    db_path = None

    # Activity tracking
    _activity_log = []
    _activity_counter = 0
    _TOKENS_PER_FILE = 800   # realistic average tokens per source file
    _TIME_PER_FILE_S = 2.0   # estimated seconds for AI to read one file without graph

    def _log_activity(self, op, target, time_ms, files_with, files_without, extra=None):
        """Write activity to the shared SQLite activity_log table."""
        tpf = self.__class__._TOKENS_PER_FILE
        tps = self.__class__._TIME_PER_FILE_S
        tokens_with    = files_with * tpf + 400
        tokens_without = max(files_without * tpf, 1)
        reduction      = tokens_without / tokens_with
        try:
            db = self._get_db()
            db.log_activity(
                op=op, target=target, time_ms=time_ms,
                files_with=files_with, files_without=files_without,
                tokens_with=tokens_with, tokens_without=tokens_without,
                reduction=reduction,
                time_without_s=round(files_without * tps, 1),
                source="web", extra=extra or {},
            )
            db.close()
        except Exception:
            pass
    
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        
        # API endpoints
        if path == "/api/stats":
            self._json_response(self._get_stats())
        elif path == "/api/nodes":
            self._json_response(self._get_nodes())
        elif path == "/api/edges":
            self._json_response(self._get_edges())
        elif path == "/api/graph":
            self._json_response(self._get_graph_data())
        elif path == "/api/search":
            query = params.get("q", [""])[0]
            mode = params.get("mode", ["hybrid"])[0]
            self._json_response(self._search(query, mode))
        elif path == "/api/blast":
            filepath = params.get("file", [""])[0]
            self._json_response(self._blast(filepath))
        elif path == "/api/compare":
            filepath = params.get("file", [""])[0]
            self._json_response(self._compare(filepath))
        elif path == "/api/activity":
            self._json_response(self._get_activity())
        elif path == "/api/token-scenarios":
            self._json_response(self._get_token_scenarios())
        elif path == "/" or path == "/index.html":
            self._serve_dashboard()
        else:
            super().do_GET()
    
    def _get_db(self):
        db_path = self.db_path or os.path.join(
            self.project_path, ".code-review-graph", "graph.db"
        )
        return GraphDB(db_path)
    
    def _json_response(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())
    
    def _get_stats(self):
        db = self._get_db()
        stats = db.get_stats()
        db.close()
        return stats
    
    def _get_nodes(self):
        db = self._get_db()
        nodes = db.get_all_nodes()
        db.close()
        return nodes
    
    def _get_edges(self):
        db = self._get_db()
        edges = db.get_all_edges()
        db.close()
        return edges
    
    def _get_graph_data(self):
        db = self._get_db()
        nodes = db.get_all_nodes()
        edges = db.get_all_edges()
        db.close()
        
        # Format for D3.js force graph
        node_ids = {n["id"] for n in nodes}
        d3_nodes = [{
            "id": n["id"], "name": n["name"], "type": n["type"],
            "file": n["file"], "language": n["language"],
            "signature": n["signature"], "docstring": n["docstring"],
            "is_test": n["is_test"],
        } for n in nodes]
        
        d3_edges = []
        name_to_id = {}
        for n in nodes:
            name_to_id[n["name"]] = n["id"]
        
        for e in edges:
            source = e.get("from_id") or name_to_id.get(e["from_name"], "")
            target = e.get("to_id") or name_to_id.get(e["to_name"], "")
            if source and target and source in node_ids and target in node_ids:
                d3_edges.append({
                    "source": source, "target": target,
                    "type": e["type"],
                })
        
        return {"nodes": d3_nodes, "links": d3_edges}
    
    def _search(self, query, mode):
        if not query:
            return {"results": [], "query": ""}
        t0 = time.time()
        db = self._get_db()
        engine = EmbeddingEngine()

        if mode == "keyword":
            results = db.keyword_search(query)
            for r in results:
                r["score"] = 1.0
        elif mode == "semantic":
            results = engine.semantic_search(db, query)
        else:
            results = hybrid_search(db, engine, query)

        stats = db.get_stats()
        db.close()
        time_ms = (time.time() - t0) * 1000

        result_files = len({r.get("file", "") for r in results if r.get("file")})
        total_files = stats.get("total_files", 0)

        self._log_activity(
            op=f"search:{mode}",
            target=query,
            time_ms=time_ms,
            files_with=result_files,
            files_without=total_files,
            extra={"result_count": len(results), "mode": mode},
        )

        tpf = self.__class__._TOKENS_PER_FILE
        tokens_with    = result_files * tpf + 400
        tokens_without = max(total_files * tpf, 1)
        reduction      = round(tokens_without / tokens_with, 1)

        return {
            "results": results,
            "query": query,
            "mode": mode,
            "time_ms": round(time_ms),
            "token_stats": {
                "files_with": result_files,
                "files_without": total_files,
                "tokens_with": tokens_with,
                "tokens_without": tokens_without,
                "reduction": reduction,
            },
        }
    
    def _blast(self, filepath):
        if not filepath:
            return {"error": "No file specified"}
        t0 = time.time()
        db = self._get_db()
        analyzer = BlastRadiusAnalyzer(db)
        result = analyzer.analyze_file(filepath)
        stats = db.get_stats()
        db.close()
        time_ms = (time.time() - t0) * 1000

        total_files = stats.get("total_files", 0)
        self._log_activity(
            op="blast",
            target=filepath,
            time_ms=time_ms,
            files_with=result.get("affected_file_count", 1),
            files_without=total_files,
            extra={
                "risk_level": result.get("risk_level", "low"),
                "risk_score": result.get("risk_score", 0),
                "direct_count": len(result.get("directly_affected", [])),
            },
        )

        result["time_ms"] = round(time_ms)
        return result
    
    def _compare(self, filepath):
        if not filepath:
            return {"error": "No file specified"}
        db = self._get_db()
        stats = db.get_stats()
        analyzer = BlastRadiusAnalyzer(db)
        result = analyzer.compare_with_without_graph(filepath, stats["total_files"])
        db.close()
        return result
    
    def _get_activity(self):
        """Return recent activity from shared SQLite log (web + Claude MCP)."""
        db = self._get_db()
        items  = db.get_activity(limit=30)
        totals = db.get_activity_totals()
        db.close()
        return {"items": items, "totals": totals}

    def _get_token_scenarios(self):
        """Return multi-scenario token comparison table."""
        db = self._get_db()
        stats = db.get_stats()
        db.close()
        total_files = stats.get("total_files", 0)
        tpf = self.__class__._TOKENS_PER_FILE
        tps = self.__class__._TIME_PER_FILE_S
        avg_blast_files = max(1, round(total_files * 0.08))

        scenarios = [
            {
                "name": "Full codebase read",
                "desc": "AI reads every file (no graph)",
                "tokens": total_files * tpf,
                "time_s": round(total_files * tps, 1),
                "files": total_files,
                "with_graph": False,
            },
            {
                "name": "Blast radius analysis",
                "desc": "Graph traces impact of one file change",
                "tokens": avg_blast_files * tpf + 400,
                "time_s": 0.08,
                "files": avg_blast_files,
                "with_graph": True,
            },
            {
                "name": "Semantic / hybrid search",
                "desc": "Graph returns top-10 matching functions",
                "tokens": 10 * 120 + 400,
                "time_s": 0.15,
                "files": 10,
                "with_graph": True,
            },
            {
                "name": "Targeted function lookup",
                "desc": "Graph finds callers + callees for one fn",
                "tokens": 3 * tpf + 400,
                "time_s": 0.03,
                "files": 3,
                "with_graph": True,
            },
        ]
        return {"scenarios": scenarios, "total_files": total_files}

    def _serve_dashboard(self):
        frontend_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "frontend", "index.html"
        )
        try:
            with open(frontend_path, "r") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(content.encode())
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Dashboard not found. Ensure frontend/index.html exists.")
    
    def log_message(self, format, *args):
        # Quiet logging
        pass


def _get_local_ip() -> str:
    """Get the machine's LAN IP address."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def start_server(project_path: str, port: int = 8000, db_path: str = None, open_browser: bool = True):
    """Start the dashboard web server, auto-building the graph if needed."""
    resolved_db = db_path or os.path.join(project_path, ".code-review-graph", "graph.db")

    # Auto-populate: build graph if DB missing or empty
    if not os.path.exists(resolved_db):
        print(f"\n[server] No graph found — building now for: {project_path}")
        build_graph(project_path, resolved_db)
    else:
        try:
            db = GraphDB(resolved_db)
            db.init()
            stats = db.get_stats()
            db.close()
            if stats.get("total_nodes", 0) == 0:
                print(f"\n[server] Graph empty — rebuilding for: {project_path}")
                build_graph(project_path, resolved_db)
        except Exception:
            print(f"\n[server] DB unreadable — rebuilding for: {project_path}")
            build_graph(project_path, resolved_db)

    DashboardHandler.project_path = project_path
    DashboardHandler.db_path = resolved_db

    # Start file watcher in background thread
    watcher = FileWatcher(project_path, db_path=resolved_db)
    watcher.start(background=True)

    local_ip = _get_local_ip()
    server = HTTPServer(("0.0.0.0", port), DashboardHandler)

    print(f"\n[server] Dashboard running at:")
    print(f"  Local:   http://localhost:{port}")
    print(f"  Network: http://{local_ip}:{port}")
    print(f"[server] File watcher active — graph updates on every save")
    print(f"[server] API endpoints:")
    print(f"  GET /api/stats        — Graph statistics")
    print(f"  GET /api/graph        — Full graph (D3.js format)")
    print(f"  GET /api/search?q=... — Hybrid search")
    print(f"  GET /api/blast?file=..— Blast radius analysis")
    print(f"  GET /api/compare?file=— With/without graph comparison")
    print(f"\nPress Ctrl+C to stop.\n")

    if open_browser:
        webbrowser.open(f"http://localhost:{port}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] Stopping…")
        watcher.stop()
        server.server_close()
        print("[server] Stopped")


# ─── CLI ───────────────────────────────────────────────────

def main():
    argparser = argparse.ArgumentParser(
        description="Code Graph Kit — Build and query code structure graphs"
    )
    sub = argparser.add_subparsers(dest="command")
    
    # build
    build_cmd = sub.add_parser("build", help="Parse project and build graph")
    build_cmd.add_argument("--path", default=".", help="Project root directory")
    
    # search
    search_cmd = sub.add_parser("search", help="Search the graph")
    search_cmd.add_argument("--query", "-q", required=True, help="Search query")
    search_cmd.add_argument("--path", default=".", help="Project root")
    search_cmd.add_argument("--mode", default="hybrid", choices=["keyword", "semantic", "hybrid"])
    
    # blast
    blast_cmd = sub.add_parser("blast", help="Compute blast radius")
    blast_cmd.add_argument("--file", "-f", required=True, help="Changed file path")
    blast_cmd.add_argument("--path", default=".", help="Project root")
    
    # serve
    serve_cmd = sub.add_parser("serve", help="Start dashboard server")
    serve_cmd.add_argument("--path", default=".", help="Project root")
    serve_cmd.add_argument("--port", type=int, default=8000)
    serve_cmd.add_argument("--no-open", action="store_true", help="Don't open browser automatically")
    
    # stats
    stats_cmd = sub.add_parser("stats", help="Show graph statistics")
    stats_cmd.add_argument("--path", default=".", help="Project root")

    # watch
    watch_cmd = sub.add_parser("watch", help="Watch project and auto-update graph on file changes")
    watch_cmd.add_argument("--path", default=".", help="Project root")
    watch_cmd.add_argument("--interval", type=float, default=2.0, help="Poll interval seconds (fallback)")

    # mcp
    mcp_cmd = sub.add_parser("mcp", help="Start MCP stdio server for Claude Code / Cursor / Windsurf")
    mcp_cmd.add_argument("--project", default=".", help="Project root directory")

    args = argparser.parse_args()
    
    if args.command == "build":
        build_graph(os.path.abspath(args.path))
    elif args.command == "search":
        search_graph(os.path.abspath(args.path), args.query, args.mode)
    elif args.command == "blast":
        blast_radius(os.path.abspath(args.path), args.file)
    elif args.command == "serve":
        start_server(os.path.abspath(args.path), args.port, open_browser=not args.no_open)
    elif args.command == "watch":
        watcher = FileWatcher(os.path.abspath(args.path), poll_interval=args.interval)
        watcher.start(background=False)
    elif args.command == "stats":
        db_path = os.path.join(args.path, ".code-review-graph", "graph.db")
        db = GraphDB(db_path)
        stats = db.get_stats()
        print(json.dumps(stats, indent=2))
        db.close()
    elif args.command == "mcp":
        try:
            from src.mcp_server import MCPServer
        except ImportError:
            sys.path.insert(0, _HERE)
            from mcp_server import MCPServer
        project = os.path.abspath(args.project)
        server = MCPServer(project)
        server.run()
    else:
        argparser.print_help()


if __name__ == "__main__":
    main()
