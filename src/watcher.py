"""
File Watcher Daemon — Auto-updates the graph without git hooks.

Monitors the project directory for file changes and incrementally 
re-indexes only the changed files. Uses watchdog library for 
cross-platform file system events.

Usage:
    python watcher.py --path /your/project

Or programmatically:
    watcher = FileWatcher("/your/project")
    watcher.start()  # Runs in background thread
"""

import os
import sys
import time
import hashlib
import threading
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))

try:
    from src.parser import UniversalParser
    from src.graph_db import GraphDB
    from src.language_configs import get_language_for_file, get_supported_extensions
except ImportError:
    sys.path.insert(0, _HERE)
    from parser import UniversalParser
    from graph_db import GraphDB
    from language_configs import get_language_for_file, get_supported_extensions


class FileWatcher:
    """
    Watches a project directory and incrementally updates the graph
    when files change. No git hooks needed.
    
    How it works:
    1. On file save → detect via polling or watchdog events
    2. Compute SHA-256 hash of changed file
    3. Compare with stored hash in DB
    4. If different → re-parse only that file
    5. Update nodes + edges in SQLite
    
    Typically completes in <2 seconds even for large projects.
    """
    
    def __init__(self, project_path: str, db_path: str = None, poll_interval: float = 2.0):
        self.project_path = os.path.abspath(project_path)
        self.db_path = db_path or os.path.join(
            self.project_path, ".code-review-graph", "graph.db"
        )
        self.poll_interval = poll_interval
        self.parser = UniversalParser()
        self.supported_extensions = get_supported_extensions()
        self._running = False
        self._thread = None
        self._file_hashes = {}  # filepath → sha256
        self._pending_deletes = {}  # hash → (filepath, timestamp) — for rename detection
        
        # Directories to ignore
        self.ignore_dirs = {
            "node_modules", ".git", "__pycache__", ".venv", "venv",
            ".tox", "dist", "build", ".code-review-graph", ".idea",
            ".vscode", "vendor", "target", ".next", ".nuxt",
        }
    
    def _hash_file(self, filepath: str) -> str:
        """Compute SHA-256 hash of a file's contents."""
        try:
            with open(filepath, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()[:16]
        except (OSError, IOError):
            return ""
    
    def _scan_all_files(self) -> dict:
        """Scan project and return {filepath: hash} for all supported files."""
        file_hashes = {}
        for root, dirs, files in os.walk(self.project_path):
            dirs[:] = [d for d in dirs if d not in self.ignore_dirs]
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in self.supported_extensions:
                    fpath = os.path.join(root, fname)
                    rel_path = os.path.relpath(fpath, self.project_path)
                    file_hashes[rel_path] = self._hash_file(fpath)
        return file_hashes
    
    def _incremental_update(self, changed_files: list, deleted_files: list):
        """Re-parse only changed files and update the graph.

        Rename detection: a delete + create with the same file hash within a 5s
        window is treated as a rename. Node IDs and embeddings are preserved via
        db.rename_file() instead of the usual clear + re-parse cycle.
        """
        if not changed_files and not deleted_files:
            return

        now = time.time()
        db = GraphDB(self.db_path)
        start = now

        # Expire stale pending deletes (genuine deletes with no matching create)
        stale = [h for h, (p, t) in self._pending_deletes.items() if now - t >= 5.0]
        for h in stale:
            old_path, _ = self._pending_deletes.pop(h)
            db.clear_file(old_path)
            print(f"  [watch] Removed: {old_path}")

        # Stage deletes: record hash from DB, defer clearing to detect renames
        for filepath in deleted_files:
            stored_hash = db.get_file_hash(filepath)
            if stored_hash:
                self._pending_deletes[stored_hash] = (filepath, now)
            else:
                db.clear_file(filepath)
                print(f"  [watch] Removed: {filepath}")

        # Process creates/changes — detect renames via hash fingerprint
        total_nodes = 0
        total_edges = 0
        for filepath in changed_files:
            full_path = os.path.join(self.project_path, filepath)
            if not os.path.exists(full_path):
                continue

            new_hash = self._hash_file(full_path)

            if new_hash in self._pending_deletes:
                old_path, _ = self._pending_deletes.pop(new_hash)
                db.rename_file(old_path, filepath)
                print(f"  [watch] Renamed: {old_path} → {filepath}")
                continue

            db.clear_file(filepath)
            nodes, edges = self.parser.parse_file(full_path, self.project_path)
            if nodes:
                db.upsert_nodes(nodes)
                total_nodes += len(nodes)
            if edges:
                db.upsert_edges(edges)
                total_edges += len(edges)
            db.set_file_hash(filepath, new_hash, len(nodes))
            print(f"  [watch] Updated: {filepath} ({len(nodes)} nodes, {len(edges)} edges)")

        if total_nodes or total_edges:
            elapsed = time.time() - start
            n_changed = len([f for f in changed_files if os.path.exists(
                os.path.join(self.project_path, f))])
            print(f"  [watch] Incremental update: {n_changed} files in {elapsed:.2f}s "
                  f"({total_nodes} nodes, {total_edges} edges)")

        db.close()
    
    def _poll_loop(self):
        """Main polling loop — checks for file changes periodically."""
        print(f"[watch] Monitoring: {self.project_path}")
        print(f"[watch] Poll interval: {self.poll_interval}s")
        print(f"[watch] Supported: {len(self.supported_extensions)} file extensions")
        print(f"[watch] Press Ctrl+C to stop\n")
        
        # Initial scan
        self._file_hashes = self._scan_all_files()
        print(f"[watch] Initial scan: {len(self._file_hashes)} files tracked\n")
        
        while self._running:
            time.sleep(self.poll_interval)
            
            # Re-scan
            current_hashes = self._scan_all_files()
            
            # Find changes
            changed = []
            deleted = []
            
            # Check for new or modified files
            for filepath, new_hash in current_hashes.items():
                old_hash = self._file_hashes.get(filepath)
                if old_hash is None or old_hash != new_hash:
                    changed.append(filepath)
            
            # Check for deleted files
            for filepath in self._file_hashes:
                if filepath not in current_hashes:
                    deleted.append(filepath)
            
            if changed or deleted:
                self._incremental_update(changed, deleted)
                self._file_hashes = current_hashes
    
    def start(self, background: bool = False):
        """Start watching for file changes.

        Args:
            background: If True, runs in a daemon thread (non-blocking)
        """
        self._running = True

        # Pick watchdog or polling
        try:
            import watchdog  # noqa: F401 — just testing availability
            target = self._start_watchdog
        except ImportError:
            print("[watch] watchdog not installed, using polling fallback")
            print("[watch] pip install watchdog (for better performance)\n")
            target = self._poll_loop

        if background:
            self._thread = threading.Thread(target=target, daemon=True)
            self._thread.start()
        else:
            try:
                target()
            except KeyboardInterrupt:
                self.stop()
    
    def _start_watchdog(self):
        """Use watchdog library for efficient file system events."""
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
        
        watcher_self = self
        
        class Handler(FileSystemEventHandler):
            def __init__(self):
                self._debounce = {}
                self._lock = threading.Lock()
            
            def _should_process(self, path):
                ext = os.path.splitext(path)[1].lower()
                if ext not in watcher_self.supported_extensions:
                    return False
                # Check ignore dirs
                rel = os.path.relpath(path, watcher_self.project_path)
                parts = rel.split(os.sep)
                return not any(p in watcher_self.ignore_dirs for p in parts)
            
            def _handle(self, event_path, is_delete=False):
                if not self._should_process(event_path):
                    return
                rel_path = os.path.relpath(event_path, watcher_self.project_path)
                
                # Debounce: ignore rapid repeated events for same file
                now = time.time()
                with self._lock:
                    last = self._debounce.get(rel_path, 0)
                    if now - last < 1.0:
                        return
                    self._debounce[rel_path] = now
                
                if is_delete:
                    watcher_self._incremental_update([], [rel_path])
                else:
                    watcher_self._incremental_update([rel_path], [])
            
            def on_modified(self, event):
                if not event.is_directory:
                    self._handle(event.src_path)
            
            def on_created(self, event):
                if not event.is_directory:
                    self._handle(event.src_path)
            
            def on_deleted(self, event):
                if not event.is_directory:
                    self._handle(event.src_path, is_delete=True)
        
        observer = Observer()
        observer.schedule(Handler(), self.project_path, recursive=True)
        observer.start()
        
        print(f"[watch] Monitoring (watchdog): {self.project_path}")
        print(f"[watch] Press Ctrl+C to stop\n")
        
        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
        observer.join()
    
    def stop(self):
        """Stop the watcher."""
        self._running = False
        print("\n[watch] Stopped")


def main():
    argparser = argparse.ArgumentParser(description="Code Graph Kit — File Watcher")
    argparser.add_argument("--path", default=".", help="Project root directory")
    argparser.add_argument("--interval", type=float, default=2.0, help="Poll interval (seconds)")
    args = argparser.parse_args()
    
    watcher = FileWatcher(
        project_path=os.path.abspath(args.path),
        poll_interval=args.interval,
    )
    watcher.start()


if __name__ == "__main__":
    main()
