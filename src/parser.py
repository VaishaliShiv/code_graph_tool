"""
Universal Tree-sitter parser engine.

Parses any supported language into a common set of nodes (functions, classes, imports)
and edges (calls, imports, member_of, tests). Language-specific behavior is driven
entirely by the config dicts in language_configs.py.

Usage:
    parser = UniversalParser()
    nodes, edges = parser.parse_file("auth/service.py")
"""

import os
import hashlib
import re
import fnmatch
from dataclasses import dataclass, field
from typing import Optional

try:
    from src.language_configs import get_language_for_file, LANGUAGE_CONFIGS
except ImportError:
    from language_configs import get_language_for_file, LANGUAGE_CONFIGS


@dataclass
class Node:
    """A code entity: function, class, or module."""
    id: str = ""
    name: str = ""
    type: str = ""         # "function" | "class" | "module"
    language: str = ""
    file: str = ""
    line: int = 0
    end_line: int = 0
    signature: str = ""
    docstring: str = ""
    sha256: str = ""
    is_test: bool = False
    parent_class: str = ""  # For methods: which class they belong to
    body_preview: str = ""  # First ~300 chars of function body for richer embeddings

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "language": self.language,
            "file": self.file,
            "line": self.line,
            "end_line": self.end_line,
            "signature": self.signature,
            "docstring": self.docstring,
            "sha256": self.sha256,
            "is_test": self.is_test,
            "parent_class": self.parent_class,
            "body_preview": self.body_preview,
        }


@dataclass 
class Edge:
    """A relationship between two nodes."""
    from_id: str = ""
    to_id: str = ""
    from_name: str = ""
    to_name: str = ""
    type: str = ""          # "calls" | "imports" | "member_of" | "inherits" | "tests"
    file: str = ""
    line: int = 0
    
    def to_dict(self) -> dict:
        return {
            "from_id": self.from_id,
            "to_id": self.to_id,
            "from_name": self.from_name,
            "to_name": self.to_name,
            "type": self.type,
            "file": self.file,
            "line": self.line,
        }


def _generate_id(file: str, name: str, line: int) -> str:
    """Generate a stable unique ID for a node."""
    raw = f"{file}::{name}::{line}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _hash_content(content: str) -> str:
    """SHA-256 hash of file/node content for change detection."""
    return hashlib.sha256(content.encode()).hexdigest()[:16]


# Names that look like function calls syntactically but are NOT project-level
# edges worth tracking — builtins, stdlib methods, exception constructors,
# common string/dict/list operations. Keeping these out of the graph prevents
# noise in query_graph callees and graph-enriched embeddings.
_CALL_DENYLIST = frozenset({
    # Python builtins
    "print", "len", "str", "int", "float", "list", "dict", "set", "tuple",
    "range", "enumerate", "isinstance", "hasattr", "getattr", "setattr",
    "delattr", "super", "type", "bool", "any", "all", "map", "filter",
    "sorted", "reversed", "zip", "abs", "round", "max", "min", "sum",
    "next", "iter", "hash", "id", "vars", "dir", "repr", "bytes",
    "bytearray", "object", "property", "classmethod", "staticmethod",
    "open", "input", "format", "chr", "ord",
    # Common stdlib method names (called on objects, not project functions)
    "append", "extend", "insert", "remove", "pop", "clear", "copy",
    "update", "get", "items", "keys", "values", "setdefault",
    "split", "join", "strip", "lstrip", "rstrip", "replace",
    "lower", "upper", "title", "startswith", "endswith", "find", "includes",
    "indexOf", "slice", "substring", "trim", "trimStart", "trimEnd",
    "encode", "decode", "read", "write", "close", "seek", "tell",
    "loads", "dumps", "load", "dump",
    "match", "search", "findall", "sub", "compile",
    "time", "sleep", "now", "strftime", "strptime",
    "get", "post", "put", "delete",
    # Exception constructors — these are raises, not real calls
    "ValueError", "TypeError", "KeyError", "IndexError", "AttributeError",
    "RuntimeError", "Exception", "PermissionError", "NotImplementedError",
    "StopIteration", "OSError", "IOError", "FileNotFoundError",
    "ImportError", "NameError", "AssertionError", "ZeroDivisionError",
    "OverflowError", "MemoryError", "RecursionError",
    # Common test helpers
    "assertEqual", "assertRaises", "assertTrue", "assertFalse",
    "assertIsNone", "assertIsNotNone", "assertIn", "assertNotIn",
    "setUp", "tearDown", "mock", "patch", "MagicMock",
})


# Auto-generated files produce noisy nodes with no semantic value — skip them
_GENERATED_FILE_PATTERNS = [
    r'_pb2\.py$', r'_pb2_grpc\.py$',          # protobuf
    r'[/\\]migrations[/\\]\d+_',               # Django/Alembic migrations
    r'\.generated\.',                           # Generic generated marker
]
_GENERATED_CONTENT_MARKERS = [
    '# Generated by', '# DO NOT EDIT', '# @generated', '// Code generated by',
]


class UniversalParser:
    """Parses source files and extracts nodes + edges.

    Engine priority:
      1. tree-sitter (AST-level, accurate for all 15+ languages)
         Needs: pip install tree-sitter-languages
         OR:    pip install tree-sitter tree-sitter-python tree-sitter-javascript …
      2. Regex fallback (built-in, no extra deps)
         Good quality for Python; basic for JS/TS/Go/Java/Rust.

    Language support is driven by LANGUAGE_CONFIGS in language_configs.py.
    Adding a new language = one dict entry there, zero parser changes.
    """

    def __init__(self):
        self._parsers: dict = {}          # cache: language → tree-sitter Parser
        self._ts_engine: str | None = None  # "tsl" | "individual" | None
        self._probe_treesitter()

    # ─── Tree-sitter engine detection ──────────────────────────

    def _probe_treesitter(self):
        """Detect which tree-sitter backend is available."""
        # Option A: tree-sitter-languages (one pip package, bundles all grammars)
        try:
            from tree_sitter_languages import get_parser  # noqa: F401
            self._ts_engine = "tsl"
            print("[parser] tree-sitter-languages detected — AST parsing enabled")
            return
        except ImportError:
            pass

        # Option B: tree-sitter >= 0.22 + individual grammar packages
        try:
            from tree_sitter import Parser as _TSParser, Language as _TSLang  # noqa: F401
            self._ts_engine = "individual"
            print("[parser] tree-sitter detected — AST parsing enabled (individual grammars)")
            return
        except ImportError:
            pass

        print("[parser] tree-sitter not installed — using regex fallback")
        print("[parser] For full multi-language support: pip install tree-sitter-languages")

    def _get_ts_parser(self, language: str):
        """Return a cached tree-sitter Parser for the language, or None."""
        if language in self._parsers:
            return self._parsers[language]

        parser = None
        grammar = LANGUAGE_CONFIGS.get(language, {}).get("grammar", f"tree_sitter_{language}")

        if self._ts_engine == "tsl":
            try:
                from tree_sitter_languages import get_parser
                parser = get_parser(language)
            except Exception:
                pass

        elif self._ts_engine == "individual":
            try:
                import importlib
                from tree_sitter import Language, Parser as TSParser
                mod = importlib.import_module(grammar)
                # Support both old (.language attribute) and new (.language() callable)
                lang_fn = getattr(mod, "language", None)
                if callable(lang_fn):
                    lang = Language(lang_fn())
                else:
                    lang = Language(lang_fn)
                parser = TSParser(lang)
            except Exception:
                # Try old-style set_language API
                try:
                    import importlib
                    from tree_sitter import Language, Parser as TSParser
                    mod = importlib.import_module(grammar)
                    p = TSParser()
                    p.set_language(Language(mod.language()))
                    parser = p
                except Exception:
                    pass

        self._parsers[language] = parser
        return parser

    # ─── Dispatch ──────────────────────────────────────────────

    def parse_file(self, filepath: str, base_path: str = "") -> tuple[list[Node], list[Edge]]:
        """Parse a source file — tree-sitter if available, regex fallback otherwise."""
        language = get_language_for_file(filepath)
        if language is None:
            return [], []

        rel_path = os.path.relpath(filepath, base_path) if base_path else filepath

        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                source = f.read()
        except (OSError, IOError):
            return [], []

        # Skip auto-generated files — noisy nodes, no semantic value
        for pat in _GENERATED_FILE_PATTERNS:
            if re.search(pat, filepath):
                return [], []
        first_500 = source[:500]
        for marker in _GENERATED_CONTENT_MARKERS:
            if marker in first_500:
                return [], []

        file_hash = _hash_content(source)

        # Try tree-sitter first (accurate AST for any language)
        if self._ts_engine:
            ts_parser = self._get_ts_parser(language)
            if ts_parser:
                return self._parse_with_treesitter(source, rel_path, file_hash, language, ts_parser)

        # Regex fallback — tuned parsers for common languages
        if language == "python":
            return self._parse_python(source, rel_path, file_hash)
        elif language in ("javascript", "typescript", "tsx"):
            return self._parse_js_ts(source, rel_path, file_hash, language)
        elif language == "go":
            return self._parse_go(source, rel_path, file_hash)
        elif language == "java":
            return self._parse_java(source, rel_path, file_hash)
        elif language == "rust":
            return self._parse_rust(source, rel_path, file_hash)
        else:
            return self._parse_generic(source, rel_path, file_hash, language)

    # ─── Tree-sitter universal engine ──────────────────────────

    def _parse_with_treesitter(self, source: str, filepath: str, file_hash: str,
                                language: str, ts_parser) -> tuple[list[Node], list[Edge]]:
        """AST-level parsing via tree-sitter — works for any language in LANGUAGE_CONFIGS.

        Uses LANGUAGE_CONFIGS[language] to know which AST node types are functions,
        classes, imports, and calls — so adding a new language needs zero parser changes.
        """
        config = LANGUAGE_CONFIGS.get(language, {})
        node_types = config.get("node_types", {})
        fn_types   = set(node_types.get("function", []))
        cls_types  = set(node_types.get("class",    []))
        imp_types  = set(node_types.get("import",   []))
        call_types = set(node_types.get("call",     []))

        source_bytes = source.encode("utf-8", errors="replace")
        tree = ts_parser.parse(source_bytes)
        lines = source.split("\n")

        is_test_file = self._ts_is_test_file(filepath, language, config)

        nodes: list[Node] = []
        edges: list[Edge] = []

        def walk(ts_node, current_class: str = "", current_func: str = ""):
            ntype = ts_node.type

            # ── Function / method ──────────────────────────────
            if ntype in fn_types:
                name = self._ts_extract_name(ts_node, config, "function", source_bytes)
                if name:
                    line  = ts_node.start_point[0] + 1
                    eline = ts_node.end_point[0]   + 1
                    sig   = lines[ts_node.start_point[0]][:160].strip()
                    doc   = self._ts_extract_docstring(ts_node, language, source_bytes)
                    is_test = is_test_file or self._ts_is_test_func(name, language, config)
                    _body_lines = lines[ts_node.start_point[0] + 1 : ts_node.end_point[0]]
                    _body_preview = " ".join(l.strip() for l in _body_lines[:15] if l.strip())[:300]
                    nodes.append(Node(
                        id=_generate_id(filepath, name, line),
                        name=name, type="function", language=language,
                        file=filepath, line=line, end_line=eline,
                        signature=sig, docstring=doc,
                        sha256=file_hash, is_test=is_test,
                        parent_class=current_class,
                        body_preview=_body_preview,
                    ))
                    if current_class:
                        edges.append(Edge(
                            from_name=name, to_name=current_class,
                            type="member_of", file=filepath, line=line,
                        ))
                    for child in ts_node.children:
                        walk(child, current_class, name)
                    return

            # ── Class / struct / interface ─────────────────────
            if ntype in cls_types:
                name = self._ts_extract_name(ts_node, config, "class", source_bytes)
                if name:
                    line  = ts_node.start_point[0] + 1
                    eline = ts_node.end_point[0]   + 1
                    sig   = lines[ts_node.start_point[0]][:160].strip()
                    nodes.append(Node(
                        id=_generate_id(filepath, name, line),
                        name=name, type="class", language=language,
                        file=filepath, line=line, end_line=eline,
                        signature=sig, sha256=file_hash,
                    ))
                    for child in ts_node.children:
                        walk(child, name, current_func)
                    return

            # ── Import ────────────────────────────────────────
            if ntype in imp_types:
                module = self._ts_extract_import(ts_node, language, config, source_bytes)
                if module:
                    mod_id = os.path.splitext(filepath)[0].replace(os.sep, ".")
                    edges.append(Edge(
                        from_name=mod_id, to_name=module,
                        type="imports", file=filepath,
                        line=ts_node.start_point[0] + 1,
                    ))

            # ── Call ──────────────────────────────────────────
            if ntype in call_types and current_func:
                called = self._ts_extract_call(ts_node, source_bytes)
                if called and called not in _CALL_DENYLIST:
                    edges.append(Edge(
                        from_name=current_func, to_name=called,
                        type="calls", file=filepath,
                        line=ts_node.start_point[0] + 1,
                    ))

            for child in ts_node.children:
                walk(child, current_class, current_func)

        walk(tree.root_node)
        return nodes, edges

    # ─── Tree-sitter helpers ───────────────────────────────────

    def _ts_extract_name(self, ts_node, config: dict, kind: str, src: bytes) -> str:
        """Extract identifier name from a function/class AST node using lang config."""
        extraction = config.get("name_extraction", {}).get(kind, {})
        target_type = extraction.get("child_type", "identifier")
        nested      = extraction.get("nested")

        for child in ts_node.children:
            if child.type == target_type:
                if nested:
                    for sub in child.children:
                        if sub.type == nested.get("child_type", "identifier"):
                            return src[sub.start_byte:sub.end_byte].decode("utf-8", errors="replace")
                return src[child.start_byte:child.end_byte].decode("utf-8", errors="replace")

        # Fallback: find first identifier-like child.
        # property_identifier covers JS/TS method names inside classes.
        for child in ts_node.children:
            if child.type in ("identifier", "type_identifier", "property_identifier", "name"):
                return src[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
        return ""

    def _ts_extract_docstring(self, fn_node, language: str, src: bytes) -> str:
        """Extract docstring/comment from function body — best-effort."""
        if language not in ("python",):
            return ""
        # Python: body block → first expression_statement → string
        for child in fn_node.children:
            if child.type == "block":
                for stmt in child.children:
                    if stmt.type == "expression_statement":
                        for s in stmt.children:
                            if s.type == "string":
                                raw = src[s.start_byte:s.end_byte].decode("utf-8", errors="replace")
                                return raw.strip('"""').strip("'''").strip('"').strip("'").strip()[:500]
                    break  # only check first statement
        return ""

    def _ts_extract_call(self, call_node, src: bytes) -> str:
        """Extract the function name from a call expression node.

        AST layout for obj.method(args):
            call
              attribute            ← first child (function being called)
                identifier "obj"   ← object (ignore)
                "."
                identifier "method" ← method name (want this — LAST identifier)
              argument_list

        AST layout for func(args):
            call
              identifier "func"    ← direct call (want this)
              argument_list
        """
        for child in call_node.children:
            # Direct call: foo(...)
            if child.type == "identifier":
                return src[child.start_byte:child.end_byte].decode("utf-8", errors="replace")

            # Method/attribute call: obj.method(...)
            # Get the LAST identifier child — that's the method name after the dot.
            if child.type in ("attribute", "member_expression", "field_expression",
                              "selector_expression", "scoped_identifier"):
                last_id = None
                for sub in child.children:
                    if sub.type in ("identifier", "property_identifier", "field_identifier",
                                    "type_identifier"):
                        last_id = src[sub.start_byte:sub.end_byte].decode("utf-8", errors="replace")
                if last_id:
                    return last_id
        return ""

    def _ts_extract_import(self, imp_node, language: str, config: dict, src: bytes) -> str:
        """Extract module/package name from import node — best-effort."""
        # Ruby: filter to require/require_relative calls only
        import_filter = config.get("import_filter", {})
        if import_filter:
            fn_names = import_filter.get("function_names", [])
            first_id = ""
            for ch in imp_node.children:
                if ch.type == "identifier":
                    first_id = src[ch.start_byte:ch.end_byte].decode("utf-8", errors="replace")
                    break
            if fn_names and first_id not in fn_names:
                return ""

        # Find string literal in import (module path)
        for child in imp_node.children:
            if child.type in ("dotted_name", "module_path",
                              "scoped_identifier", "namespace_use_path"):
                return src[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
            if child.type in ("string", "interpreted_string_literal", "raw_string_literal"):
                raw = src[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
                return raw.strip('"').strip("'").strip("`")
            if child.type == "identifier":
                return src[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
        return ""

    def _ts_is_test_file(self, filepath: str, language: str, config: dict) -> bool:
        patterns = config.get("test_patterns", {}).get("file", [])
        base = os.path.basename(filepath)
        return any(fnmatch.fnmatch(base, p) for p in patterns)

    def _ts_is_test_func(self, name: str, language: str, config: dict) -> bool:
        tp = config.get("test_patterns", {})
        for prefix in tp.get("function_prefix", []):
            if name.startswith(prefix):
                return True
        for fn in tp.get("function_name", []):
            if name == fn:
                return True
        return False
    
    def parse_project(self, project_path: str) -> tuple[list[Node], list[Edge]]:
        """Parse all supported files in a project directory.
        
        Args:
            project_path: Root directory of the project
            
        Returns:
            Tuple of (all_nodes, all_edges)
        """
        try:
            from src.language_configs import get_supported_extensions
        except ImportError:
            from language_configs import get_supported_extensions
        
        all_nodes = []
        all_edges = []
        supported = get_supported_extensions()
        
        ignore_dirs = {
            "node_modules", ".git", "__pycache__", ".venv", "venv",
            ".tox", "dist", "build", ".code-review-graph", ".idea",
            ".vscode", "vendor", "target",
        }
        
        for root, dirs, files in os.walk(project_path):
            # Skip ignored directories
            dirs[:] = [d for d in dirs if d not in ignore_dirs]
            
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in supported:
                    fpath = os.path.join(root, fname)
                    nodes, edges = self.parse_file(fpath, project_path)
                    all_nodes.extend(nodes)
                    all_edges.extend(edges)
        
        # Resolve cross-file edges (match call names to defined functions)
        all_nodes, all_edges = self._resolve_cross_file_edges(all_nodes, all_edges)
        
        return all_nodes, all_edges
    
    # ─── Python parser ─────────────────────────────────────
    
    def _parse_python(self, source: str, filepath: str, file_hash: str):
        nodes = []
        edges = []
        lines = source.split("\n")
        
        is_test_file = any(
            fnmatch.fnmatch(os.path.basename(filepath), pat)
            for pat in ["test_*.py", "*_test.py"]
        )
        
        current_class = None
        current_class_line = 0
        class_indent = 0
        
        # ── Pass 1: Extract classes and functions ──
        for i, line in enumerate(lines, 1):
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            
            # Detect class definitions
            class_match = re.match(r'^class\s+(\w+)\s*(?:\((.*?)\))?\s*:', stripped)
            if class_match:
                class_name = class_match.group(1)
                bases = class_match.group(2) or ""
                class_indent = indent
                current_class = class_name
                current_class_line = i
                
                docstring = self._extract_python_docstring(lines, i)
                
                node = Node(
                    id=_generate_id(filepath, class_name, i),
                    name=class_name,
                    type="class",
                    language="python",
                    file=filepath,
                    line=i,
                    signature=f"class {class_name}({bases})" if bases else f"class {class_name}",
                    docstring=docstring,
                    sha256=file_hash,
                    is_test=class_name.startswith("Test") and is_test_file,
                )
                nodes.append(node)
                
                # Inheritance edges
                if bases:
                    for base in bases.split(","):
                        base = base.strip()
                        if base and base not in ("object", "ABC"):
                            edges.append(Edge(
                                from_name=class_name,
                                to_name=base,
                                type="inherits",
                                file=filepath,
                                line=i,
                            ))
                continue
            
            # Detect function definitions
            func_match = re.match(r'^(async\s+)?def\s+(\w+)\s*\((.*?)\)\s*(?:->\s*(.+?))?\s*:', stripped)
            if func_match:
                is_async = bool(func_match.group(1))
                func_name = func_match.group(2)
                params = func_match.group(3) or ""
                return_type = func_match.group(4) or ""
                
                # Is this a method inside a class?
                parent = None
                if current_class and indent > class_indent:
                    parent = current_class
                else:
                    current_class = None
                
                prefix = "async def" if is_async else "def"
                sig = f"{prefix} {func_name}({params})"
                if return_type:
                    sig += f" -> {return_type}"
                
                docstring = self._extract_python_docstring(lines, i)
                
                is_test = (
                    func_name.startswith("test_") or 
                    (is_test_file and func_name.startswith("test"))
                )
                
                _bp_parts, _bp_chars = [], 0
                for _bl in lines[i:i + 20]:
                    _s = _bl.strip()
                    if not _s or _s.startswith('#'):
                        continue
                    _bp_parts.append(_s)
                    _bp_chars += len(_s)
                    if _bp_chars >= 300:
                        break
                node = Node(
                    id=_generate_id(filepath, func_name, i),
                    name=func_name,
                    type="function",
                    language="python",
                    file=filepath,
                    line=i,
                    signature=sig,
                    docstring=docstring,
                    sha256=file_hash,
                    is_test=is_test,
                    parent_class=parent or "",
                    body_preview=" ".join(_bp_parts)[:300],
                )
                nodes.append(node)
                
                # member_of edge for methods
                if parent:
                    edges.append(Edge(
                        from_name=func_name,
                        to_name=parent,
                        type="member_of",
                        file=filepath,
                        line=i,
                    ))
        
        # ── Pass 2: Extract imports ──
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            
            import_match = re.match(r'^from\s+([\w.]+)\s+import\s+(.+)', stripped)
            if import_match:
                module = import_match.group(1)
                names = import_match.group(2)
                for name in names.split(","):
                    name = name.strip().split(" as ")[0].strip()
                    if name:
                        edges.append(Edge(
                            from_name=os.path.splitext(filepath)[0].replace("/", "."),
                            to_name=f"{module}.{name}",
                            type="imports",
                            file=filepath,
                            line=i,
                        ))
            
            import_match2 = re.match(r'^import\s+([\w.]+)', stripped)
            if import_match2:
                module = import_match2.group(1)
                edges.append(Edge(
                    from_name=os.path.splitext(filepath)[0].replace("/", "."),
                    to_name=module,
                    type="imports",
                    file=filepath,
                    line=i,
                ))
        
        # ── Pass 3: Extract function calls ──
        func_names = {n.name for n in nodes if n.type == "function"}
        call_pattern = re.compile(r'(?:self\.|cls\.)?(\w+)\s*\(')
        getattr_pattern = re.compile(r"getattr\s*\(\s*\w+\s*,\s*['\"](\w+)['\"]")
        
        current_func = None
        current_func_indent = 0
        
        for i, line in enumerate(lines, 1):
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            
            # Track which function we're inside
            func_match = re.match(r'^(?:async\s+)?def\s+(\w+)', stripped)
            if func_match:
                current_func = func_match.group(1)
                current_func_indent = indent
                continue
            
            if current_func and indent <= current_func_indent and stripped and not stripped.startswith("#"):
                current_func = None
            
            if current_func:
                for call_match in call_pattern.finditer(stripped):
                    called = call_match.group(1)
                    if called != current_func and not called.startswith("_") or called.startswith("__"):
                        if called not in _CALL_DENYLIST:
                            edges.append(Edge(
                                from_name=current_func,
                                to_name=called,
                                type="calls",
                                file=filepath,
                                line=i,
                            ))
                for ga_match in getattr_pattern.finditer(stripped):
                    called = ga_match.group(1)
                    if called not in _CALL_DENYLIST:
                        edges.append(Edge(
                            from_name=current_func,
                            to_name=called,
                            type="calls",
                            file=filepath,
                            line=i,
                        ))
        
        # Add test edges
        if is_test_file:
            for node in nodes:
                if node.is_test:
                    # Find what this test calls
                    for edge in edges:
                        if edge.from_name == node.name and edge.type == "calls":
                            edges.append(Edge(
                                from_name=node.name,
                                to_name=edge.to_name,
                                type="tests",
                                file=filepath,
                                line=node.line,
                            ))
        
        return nodes, edges
    
    def _extract_python_docstring(self, lines: list, def_line: int) -> str:
        """Extract docstring from the line after a def/class."""
        if def_line < len(lines):
            next_line = lines[def_line].strip()  # def_line is 1-indexed, lines is 0-indexed
            if next_line.startswith('"""') or next_line.startswith("'''"):
                quote = next_line[:3]
                if next_line.endswith(quote) and len(next_line) > 6:
                    return next_line[3:-3].strip()
                # Multi-line docstring
                doc_lines = [next_line[3:]]
                for j in range(def_line + 1, min(def_line + 20, len(lines))):
                    if quote in lines[j]:
                        doc_lines.append(lines[j].strip().rstrip(quote))
                        break
                    doc_lines.append(lines[j].strip())
                return " ".join(doc_lines).strip()
        return ""
    
    # ─── JavaScript/TypeScript parser ──────────────────────
    
    def _parse_js_ts(self, source, filepath, file_hash, language):
        nodes = []
        edges = []
        lines = source.split("\n")
        
        is_test_file = any(
            fnmatch.fnmatch(os.path.basename(filepath), pat)
            for pat in ["*.test.*", "*.spec.*", "test_*.*"]
        )
        
        # Extract functions
        func_patterns = [
            re.compile(r'(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\((.*?)\)'),
            re.compile(r'(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\((.*?)\)\s*=>'),
            re.compile(r'(?:export\s+)?(?:async\s+)?(\w+)\s*\((.*?)\)\s*\{'),
        ]
        
        for i, line in enumerate(lines, 1):
            for pat in func_patterns:
                m = pat.search(line)
                if m:
                    name = m.group(1)
                    params = m.group(2) if m.lastindex >= 2 else ""
                    if name not in ("if", "for", "while", "switch", "catch", "class", "return"):
                        nodes.append(Node(
                            id=_generate_id(filepath, name, i),
                            name=name,
                            type="function",
                            language=language,
                            file=filepath,
                            line=i,
                            signature=f"function {name}({params})",
                            sha256=file_hash,
                            is_test=is_test_file,
                        ))
                    break
            
            # Classes
            class_match = re.search(r'(?:export\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?', line)
            if class_match:
                name = class_match.group(1)
                base = class_match.group(2)
                nodes.append(Node(
                    id=_generate_id(filepath, name, i),
                    name=name,
                    type="class",
                    language=language,
                    file=filepath,
                    line=i,
                    signature=f"class {name}" + (f" extends {base}" if base else ""),
                    sha256=file_hash,
                ))
                if base:
                    edges.append(Edge(from_name=name, to_name=base, type="inherits", file=filepath, line=i))
            
            # Imports
            import_match = re.search(r"import\s+.*?\s+from\s+['\"](.+?)['\"]", line)
            if import_match:
                module = import_match.group(1)
                edges.append(Edge(
                    from_name=filepath,
                    to_name=module,
                    type="imports",
                    file=filepath,
                    line=i,
                ))
        
        # Extract calls
        call_pattern = re.compile(r'(?<!function\s)(?<!class\s)(\w+)\s*\(')
        node_names = {n.name for n in nodes}
        for i, line in enumerate(lines, 1):
            for m in call_pattern.finditer(line):
                called = m.group(1)
                if called in node_names:
                    edges.append(Edge(from_name="", to_name=called, type="calls", file=filepath, line=i))
        
        return nodes, edges
    
    # ─── Go parser ─────────────────────────────────────────
    
    def _parse_go(self, source, filepath, file_hash):
        nodes = []
        edges = []
        lines = source.split("\n")
        
        is_test = filepath.endswith("_test.go")
        
        for i, line in enumerate(lines, 1):
            # Functions
            func_match = re.search(r'func\s+(?:\((\w+)\s+\*?(\w+)\)\s+)?(\w+)\s*\((.*?)\)', line)
            if func_match:
                receiver_var = func_match.group(1)
                receiver_type = func_match.group(2)
                name = func_match.group(3)
                params = func_match.group(4) or ""
                
                sig = f"func {name}({params})"
                if receiver_type:
                    sig = f"func ({receiver_var} {receiver_type}) {name}({params})"
                
                nodes.append(Node(
                    id=_generate_id(filepath, name, i),
                    name=name, type="function", language="go",
                    file=filepath, line=i, signature=sig,
                    sha256=file_hash,
                    is_test=name.startswith("Test") and is_test,
                    parent_class=receiver_type or "",
                ))
                if receiver_type:
                    edges.append(Edge(from_name=name, to_name=receiver_type, type="member_of", file=filepath, line=i))
            
            # Structs
            struct_match = re.search(r'type\s+(\w+)\s+struct\s*\{', line)
            if struct_match:
                name = struct_match.group(1)
                nodes.append(Node(
                    id=_generate_id(filepath, name, i),
                    name=name, type="class", language="go",
                    file=filepath, line=i,
                    signature=f"type {name} struct",
                    sha256=file_hash,
                ))
            
            # Imports
            import_match = re.search(r'"([\w/.-]+)"', line)
            if import_match and ("import" in source[:source.find(line)]):
                edges.append(Edge(
                    from_name=filepath, to_name=import_match.group(1),
                    type="imports", file=filepath, line=i,
                ))
        
        return nodes, edges
    
    # ─── Java parser ───────────────────────────────────────
    
    def _parse_java(self, source, filepath, file_hash):
        nodes = []
        edges = []
        lines = source.split("\n")
        
        is_test = "Test" in os.path.basename(filepath)
        
        for i, line in enumerate(lines, 1):
            # Classes
            class_match = re.search(
                r'(?:public|private|protected)?\s*(?:abstract\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?', line
            )
            if class_match:
                name = class_match.group(1)
                base = class_match.group(2)
                nodes.append(Node(
                    id=_generate_id(filepath, name, i),
                    name=name, type="class", language="java",
                    file=filepath, line=i,
                    signature=f"class {name}" + (f" extends {base}" if base else ""),
                    sha256=file_hash, is_test=is_test,
                ))
                if base:
                    edges.append(Edge(from_name=name, to_name=base, type="inherits", file=filepath, line=i))
            
            # Methods
            method_match = re.search(
                r'(?:public|private|protected)\s+(?:static\s+)?(?:\w+(?:<.*?>)?)\s+(\w+)\s*\((.*?)\)', line
            )
            if method_match:
                name = method_match.group(1)
                params = method_match.group(2)
                if name not in ("if", "for", "while", "switch", "catch"):
                    is_test_method = "@Test" in (lines[i-2] if i >= 2 else "")
                    nodes.append(Node(
                        id=_generate_id(filepath, name, i),
                        name=name, type="function", language="java",
                        file=filepath, line=i,
                        signature=f"{name}({params})",
                        sha256=file_hash, is_test=is_test_method,
                    ))
            
            # Imports
            import_match = re.search(r'import\s+([\w.]+);', line)
            if import_match:
                edges.append(Edge(
                    from_name=filepath, to_name=import_match.group(1),
                    type="imports", file=filepath, line=i,
                ))
        
        return nodes, edges
    
    # ─── Rust parser ───────────────────────────────────────
    
    def _parse_rust(self, source, filepath, file_hash):
        nodes = []
        edges = []
        lines = source.split("\n")
        
        in_test_mod = False
        prev_line_test_attr = False
        
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            
            if stripped == "#[test]":
                prev_line_test_attr = True
                continue
            if "mod tests" in stripped:
                in_test_mod = True
            
            # Functions
            func_match = re.search(r'(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*(?:<.*?>)?\s*\((.*?)\)', line)
            if func_match:
                name = func_match.group(1)
                params = func_match.group(2)
                nodes.append(Node(
                    id=_generate_id(filepath, name, i),
                    name=name, type="function", language="rust",
                    file=filepath, line=i,
                    signature=f"fn {name}({params})",
                    sha256=file_hash,
                    is_test=prev_line_test_attr or in_test_mod,
                ))
                prev_line_test_attr = False
            
            # Structs
            struct_match = re.search(r'(?:pub\s+)?struct\s+(\w+)', line)
            if struct_match:
                name = struct_match.group(1)
                nodes.append(Node(
                    id=_generate_id(filepath, name, i),
                    name=name, type="class", language="rust",
                    file=filepath, line=i,
                    signature=f"struct {name}",
                    sha256=file_hash,
                ))
            
            # Use declarations
            use_match = re.search(r'use\s+([\w:]+)', stripped)
            if use_match:
                edges.append(Edge(
                    from_name=filepath, to_name=use_match.group(1),
                    type="imports", file=filepath, line=i,
                ))
        
        return nodes, edges
    
    # ─── Generic fallback parser ───────────────────────────
    
    def _parse_generic(self, source, filepath, file_hash, language):
        """Basic regex parser for languages without dedicated support."""
        nodes = []
        edges = []
        lines = source.split("\n")
        
        config = LANGUAGE_CONFIGS.get(language, {})
        
        # Try common patterns
        func_patterns = [
            re.compile(r'(?:pub(?:lic)?|private|protected|static|async|export)?\s*(?:fun|func|fn|def|function|sub)\s+(\w+)\s*\('),
            re.compile(r'(?:pub(?:lic)?|private|protected|static)?\s+\w+\s+(\w+)\s*\('),
        ]
        class_patterns = [
            re.compile(r'(?:pub(?:lic)?|private|export)?\s*(?:class|struct|interface|trait|contract|object)\s+(\w+)'),
        ]
        
        for i, line in enumerate(lines, 1):
            for pat in func_patterns:
                m = pat.search(line)
                if m:
                    name = m.group(1)
                    if name not in ("if", "for", "while", "switch", "catch", "return", "class"):
                        nodes.append(Node(
                            id=_generate_id(filepath, name, i),
                            name=name, type="function", language=language,
                            file=filepath, line=i, sha256=file_hash,
                        ))
                    break
            
            for pat in class_patterns:
                m = pat.search(line)
                if m:
                    nodes.append(Node(
                        id=_generate_id(filepath, m.group(1), i),
                        name=m.group(1), type="class", language=language,
                        file=filepath, line=i, sha256=file_hash,
                    ))
                    break
        
        return nodes, edges
    
    # ─── Cross-file edge resolution ────────────────────────
    
    def _resolve_cross_file_edges(self, nodes, edges):
        """Match call edges to actual function definitions across files."""
        # Build name → node lookup
        name_to_nodes = {}
        for node in nodes:
            if node.name not in name_to_nodes:
                name_to_nodes[node.name] = []
            name_to_nodes[node.name].append(node)
        
        resolved_edges = []
        for edge in edges:
            if edge.type == "calls" and edge.to_name in name_to_nodes:
                targets = name_to_nodes[edge.to_name]
                for target in targets:
                    resolved_edge = Edge(
                        from_id=edge.from_id,
                        to_id=target.id,
                        from_name=edge.from_name,
                        to_name=target.name,
                        type=edge.type,
                        file=edge.file,
                        line=edge.line,
                    )
                    resolved_edges.append(resolved_edge)
            else:
                resolved_edges.append(edge)
        
        return nodes, resolved_edges
