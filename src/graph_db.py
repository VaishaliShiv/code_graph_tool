"""
SQLite Graph Database.

Stores the code graph (nodes + edges) and optional vector embeddings
in a local SQLite database. No external database server needed.

Usage:
    db = GraphDB("./my-project/.code-review-graph/graph.db")
    db.init()
    db.upsert_nodes(nodes)
    db.upsert_edges(edges)
    results = db.search_nodes("authentication")
"""

import sqlite3
import json
import os
from typing import Optional


class GraphDB:
    """SQLite-backed graph database for code structure."""
    
    def __init__(self, db_path: str = ".code-review-graph/graph.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
    
    def init(self):
        """Create all tables and indexes."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                language TEXT NOT NULL,
                file TEXT NOT NULL,
                line INTEGER NOT NULL,
                end_line INTEGER DEFAULT 0,
                signature TEXT DEFAULT '',
                docstring TEXT DEFAULT '',
                sha256 TEXT DEFAULT '',
                is_test INTEGER DEFAULT 0,
                parent_class TEXT DEFAULT '',
                body_preview TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_id TEXT DEFAULT '',
                to_id TEXT DEFAULT '',
                from_name TEXT NOT NULL,
                to_name TEXT NOT NULL,
                type TEXT NOT NULL,
                file TEXT NOT NULL,
                line INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS embeddings (
                node_id TEXT PRIMARY KEY,
                vector BLOB NOT NULL,
                model TEXT DEFAULT 'all-MiniLM-L6-v2',
                text_input TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE
            );
            
            CREATE TABLE IF NOT EXISTS file_hashes (
                file TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL,
                node_count INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file);
            CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
            CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
            CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_name);
            CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_name);
            CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(type);
            CREATE INDEX IF NOT EXISTS idx_edges_from_id ON edges(from_id);
            CREATE INDEX IF NOT EXISTS idx_edges_to_id ON edges(to_id);
        """)
        
        # Create FTS5 virtual table for full-text search
        try:
            self.conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
                    name, signature, docstring, file,
                    content='nodes',
                    content_rowid='rowid'
                )
            """)
        except sqlite3.OperationalError:
            pass  # FTS5 may already exist
        
        self.conn.commit()

        # Migrate existing DBs that predate the body_preview column
        try:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN body_preview TEXT DEFAULT ''")
            self.conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists

    def upsert_nodes(self, nodes: list):
        """Insert or update nodes in the database."""
        for node in nodes:
            d = node.to_dict() if hasattr(node, 'to_dict') else node
            self.conn.execute("""
                INSERT OR REPLACE INTO nodes
                (id, name, type, language, file, line, end_line, signature, docstring, sha256, is_test, parent_class, body_preview)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                d["id"], d["name"], d["type"], d["language"], d["file"],
                d["line"], d.get("end_line", 0), d.get("signature", ""),
                d.get("docstring", ""), d.get("sha256", ""),
                1 if d.get("is_test") else 0, d.get("parent_class", ""),
                d.get("body_preview", ""),
            ))
            
            # Update FTS index
            try:
                self.conn.execute("""
                    INSERT OR REPLACE INTO nodes_fts(rowid, name, signature, docstring, file)
                    SELECT rowid, name, signature, docstring, file FROM nodes WHERE id = ?
                """, (d["id"],))
            except sqlite3.OperationalError:
                pass
        
        self.conn.commit()
    
    def upsert_edges(self, edges: list):
        """Insert edges into the database."""
        for edge in edges:
            d = edge.to_dict() if hasattr(edge, 'to_dict') else edge
            self.conn.execute("""
                INSERT INTO edges (from_id, to_id, from_name, to_name, type, file, line)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                d.get("from_id", ""), d.get("to_id", ""),
                d["from_name"], d["to_name"], d["type"],
                d["file"], d.get("line", 0),
            ))
        self.conn.commit()
    
    def clear_file(self, filepath: str):
        """Remove all nodes and edges for a file (before re-parsing)."""
        self.conn.execute("DELETE FROM edges WHERE file = ?", (filepath,))
        self.conn.execute("DELETE FROM embeddings WHERE node_id IN (SELECT id FROM nodes WHERE file = ?)", (filepath,))
        self.conn.execute("DELETE FROM nodes WHERE file = ?", (filepath,))
        self.conn.execute("DELETE FROM file_hashes WHERE file = ?", (filepath,))
        self.conn.commit()

    def rename_file(self, old_path: str, new_path: str):
        """Update all records when a file is renamed — preserves node IDs and embeddings."""
        self.conn.execute("UPDATE nodes SET file = ? WHERE file = ?", (new_path, old_path))
        self.conn.execute("UPDATE edges SET file = ? WHERE file = ?", (new_path, old_path))
        self.conn.execute("UPDATE file_hashes SET file = ? WHERE file = ?", (new_path, old_path))
        self.conn.commit()
    
    def get_file_hash(self, filepath: str) -> Optional[str]:
        """Get the stored hash for a file."""
        row = self.conn.execute(
            "SELECT sha256 FROM file_hashes WHERE file = ?", (filepath,)
        ).fetchone()
        return row["sha256"] if row else None
    
    def set_file_hash(self, filepath: str, sha256: str, node_count: int):
        """Store the hash for a file."""
        self.conn.execute("""
            INSERT OR REPLACE INTO file_hashes (file, sha256, node_count, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """, (filepath, sha256, node_count))
        self.conn.commit()
    
    # ─── Query methods ─────────────────────────────────────
    
    def get_all_nodes(self) -> list[dict]:
        """Get all nodes in the graph."""
        rows = self.conn.execute("SELECT * FROM nodes ORDER BY file, line").fetchall()
        return [dict(r) for r in rows]
    
    def get_all_edges(self) -> list[dict]:
        """Get all edges in the graph."""
        rows = self.conn.execute("SELECT * FROM edges").fetchall()
        return [dict(r) for r in rows]
    
    def get_node(self, node_id: str) -> Optional[dict]:
        """Get a single node by ID."""
        row = self.conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        return dict(row) if row else None
    
    def get_nodes_by_file(self, filepath: str) -> list[dict]:
        """Get all nodes in a file."""
        rows = self.conn.execute(
            "SELECT * FROM nodes WHERE file = ? ORDER BY line", (filepath,)
        ).fetchall()
        return [dict(r) for r in rows]
    
    def get_callers(self, func_name: str) -> list[dict]:
        """Find all functions that call the given function."""
        rows = self.conn.execute("""
            SELECT DISTINCT e.from_name, e.file, e.line, n.type, n.signature
            FROM edges e
            LEFT JOIN nodes n ON n.name = e.from_name AND n.file = e.file
            WHERE e.to_name = ? AND e.type = 'calls'
        """, (func_name,)).fetchall()
        return [dict(r) for r in rows]
    
    def get_callees(self, func_name: str) -> list[dict]:
        """Find all functions called by the given function."""
        rows = self.conn.execute("""
            SELECT DISTINCT e.to_name, e.file, e.line
            FROM edges e
            WHERE e.from_name = ? AND e.type = 'calls'
        """, (func_name,)).fetchall()
        return [dict(r) for r in rows]
    
    def get_tests_for(self, func_name: str) -> list[dict]:
        """Find all tests that test the given function."""
        rows = self.conn.execute("""
            SELECT DISTINCT e.from_name as test_name, e.file as test_file, n.signature
            FROM edges e
            LEFT JOIN nodes n ON n.name = e.from_name
            WHERE e.to_name = ? AND e.type = 'tests'
        """, (func_name,)).fetchall()
        return [dict(r) for r in rows]
    
    def get_dependents(self, filepath: str) -> list[dict]:
        """Find all files that import from the given file."""
        module = os.path.splitext(filepath)[0].replace("/", ".")
        rows = self.conn.execute("""
            SELECT DISTINCT e.from_name, e.file
            FROM edges e
            WHERE e.to_name LIKE ? AND e.type = 'imports'
        """, (f"%{module.split('.')[-1]}%",)).fetchall()
        return [dict(r) for r in rows]
    
    def keyword_search(self, query: str, limit: int = 20) -> list[dict]:
        """Full-text search on node names, signatures, docstrings.

        Pre-processes the query to handle code naming conventions:
        - camelCase → "camel Case" (FTS5 sees both tokens)
        - snake_case → "snake case"
        - Falls back to LIKE if FTS5 unavailable
        """
        import re
        # Expand camelCase: FileWatcher → "File Watcher"
        expanded = re.sub(r'([a-z])([A-Z])', r'\1 \2', query)
        # Expand snake_case: file_watcher → "file watcher"
        expanded = expanded.replace('_', ' ')
        # Build FTS5 prefix query — each token becomes a prefix match
        tokens = [t for t in expanded.split() if t]
        fts_query = ' OR '.join(f'"{t}"*' for t in tokens) if tokens else query

        try:
            rows = self.conn.execute("""
                SELECT n.*, rank
                FROM nodes_fts fts
                JOIN nodes n ON n.rowid = fts.rowid
                WHERE nodes_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (fts_query, limit)).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            # Fallback to LIKE search
            like = f"%{query}%"
            rows = self.conn.execute("""
                SELECT * FROM nodes
                WHERE name LIKE ? OR signature LIKE ? OR docstring LIKE ?
                ORDER BY name
                LIMIT ?
            """, (like, like, like, limit)).fetchall()
            return [dict(r) for r in rows]

    def get_graph_context(self, func_name: str) -> dict:
        """Return 1-hop graph context for a function — callers, callees, tests.

        Single query set — used to enrich search results without extra tool calls.
        """
        callers = self.conn.execute("""
            SELECT DISTINCT e.from_name, e.file
            FROM edges e
            WHERE e.to_name = ? AND e.type = 'calls'
            LIMIT 10
        """, (func_name,)).fetchall()

        # Only return callees that exist as nodes in the graph — filters out
        # builtins, stdlib methods, and other noise the parser may have captured.
        callees = self.conn.execute("""
            SELECT DISTINCT e.to_name
            FROM edges e
            INNER JOIN nodes n ON n.name = e.to_name
            WHERE e.from_name = ? AND e.type = 'calls'
            LIMIT 5
        """, (func_name,)).fetchall()

        tests = self.conn.execute("""
            SELECT DISTINCT e.from_name
            FROM edges e
            WHERE e.to_name = ? AND e.type = 'tests'
            LIMIT 5
        """, (func_name,)).fetchall()

        return {
            "callers": [{"name": r["from_name"], "file": r["file"]} for r in callers],
            "callees": [r["to_name"] for r in callees],
            "tests": [r["from_name"] for r in tests],
        }
    
    def store_embedding(self, node_id: str, vector: bytes, text_input: str, model: str = "all-MiniLM-L6-v2"):
        """Store a vector embedding for a node."""
        self.conn.execute("""
            INSERT OR REPLACE INTO embeddings (node_id, vector, text_input, model)
            VALUES (?, ?, ?, ?)
        """, (node_id, vector, text_input, model))
        self.conn.commit()
    
    def get_all_embeddings(self) -> list[dict]:
        """Get all stored embeddings."""
        rows = self.conn.execute("""
            SELECT e.node_id, e.vector, e.text_input,
                   n.name, n.type, n.file, n.signature, n.docstring, n.is_test
            FROM embeddings e
            JOIN nodes n ON n.id = e.node_id
        """).fetchall()
        return [dict(r) for r in rows]
    
    def get_stats(self) -> dict:
        """Get graph statistics."""
        nodes = self.conn.execute("SELECT COUNT(*) as c FROM nodes").fetchone()["c"]
        edges = self.conn.execute("SELECT COUNT(*) as c FROM edges").fetchone()["c"]
        files = self.conn.execute("SELECT COUNT(DISTINCT file) as c FROM nodes").fetchone()["c"]
        embeddings = self.conn.execute("SELECT COUNT(*) as c FROM embeddings").fetchone()["c"]
        
        type_counts = {}
        for row in self.conn.execute("SELECT type, COUNT(*) as c FROM nodes GROUP BY type"):
            type_counts[row["type"]] = row["c"]
        
        edge_counts = {}
        for row in self.conn.execute("SELECT type, COUNT(*) as c FROM edges GROUP BY type"):
            edge_counts[row["type"]] = row["c"]
        
        lang_counts = {}
        for row in self.conn.execute("SELECT language, COUNT(*) as c FROM nodes GROUP BY language"):
            lang_counts[row["language"]] = row["c"]
        
        return {
            "total_nodes": nodes,
            "total_edges": edges,
            "total_files": files,
            "total_embeddings": embeddings,
            "node_types": type_counts,
            "edge_types": edge_counts,
            "languages": lang_counts,
        }
    
    # ── Activity log (shared across web + MCP processes via SQLite WAL) ──

    def _ensure_activity_table(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          TEXT    NOT NULL,
                op          TEXT    NOT NULL,
                target      TEXT    NOT NULL DEFAULT '',
                source      TEXT    NOT NULL DEFAULT 'web',
                time_ms     REAL    NOT NULL DEFAULT 0,
                files_with  INTEGER NOT NULL DEFAULT 0,
                files_without INTEGER NOT NULL DEFAULT 0,
                tokens_with INTEGER NOT NULL DEFAULT 0,
                tokens_without INTEGER NOT NULL DEFAULT 0,
                reduction   REAL    NOT NULL DEFAULT 1,
                time_without_s REAL NOT NULL DEFAULT 0,
                extra_json  TEXT    DEFAULT '{}'
            )
        """)
        self.conn.commit()

    def log_activity(self, op, target, time_ms, files_with, files_without,
                     tokens_with, tokens_without, reduction, time_without_s,
                     source="web", extra=None):
        """Write one activity entry. Called by both web server and MCP server."""
        self._ensure_activity_table()
        import datetime
        self.conn.execute("""
            INSERT INTO activity_log
              (ts, op, target, source, time_ms, files_with, files_without,
               tokens_with, tokens_without, reduction, time_without_s, extra_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            datetime.datetime.now().strftime("%H:%M:%S"),
            op, (target or "")[:80], source,
            round(time_ms, 2), files_with, files_without,
            tokens_with, tokens_without, round(reduction, 2),
            round(time_without_s, 1),
            json.dumps(extra or {}),
        ))
        self.conn.commit()

    def get_activity(self, limit=30):
        """Return recent activity entries, newest first."""
        try:
            self._ensure_activity_table()
            rows = self.conn.execute("""
                SELECT * FROM activity_log ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["extra"] = json.loads(d.pop("extra_json", "{}"))
                d.update(d.pop("extra"))   # flatten extra fields into top level
                result.append(d)
            return result
        except Exception:
            return []

    def get_activity_totals(self):
        """Session-level aggregate stats."""
        try:
            self._ensure_activity_table()
            row = self.conn.execute("""
                SELECT
                    COUNT(*) as count,
                    SUM(MAX(tokens_without - tokens_with, 0)) as tokens_saved,
                    SUM(time_without_s) as time_saved_s,
                    AVG(reduction) as avg_reduction
                FROM activity_log
            """).fetchone()
            return {
                "count":        row["count"] or 0,
                "tokens_saved": int(row["tokens_saved"] or 0),
                "time_saved_s": round(row["time_saved_s"] or 0, 1),
                "avg_reduction": round(row["avg_reduction"] or 0, 1),
            }
        except Exception:
            return {"count": 0, "tokens_saved": 0, "time_saved_s": 0, "avg_reduction": 0}

    def close(self):
        self.conn.close()
