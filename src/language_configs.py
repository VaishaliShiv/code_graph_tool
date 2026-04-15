"""
Language configuration for the universal Tree-sitter parser.

Each language maps its Tree-sitter AST node types to our 4 universal concepts:
  - function: how this language defines functions/methods
  - class: how this language defines classes/structs/contracts
  - import: how this language imports from other files
  - call: how this language calls functions

Adding a new language = adding one dict entry here. The parser engine stays the same.
"""

LANGUAGE_CONFIGS = {
    # ─── Python ───────────────────────────────────────────
    "python": {
        "extensions": [".py"],
        "grammar": "tree_sitter_python",
        "node_types": {
            "function": ["function_definition"],
            "class": ["class_definition"],
            "import": ["import_statement", "import_from_statement"],
            "call": ["call"],
        },
        "test_patterns": {
            "file": ["test_*.py", "*_test.py"],
            "function_prefix": ["test_"],
            "class_prefix": ["Test"],
        },
        "name_extraction": {
            "function": {"child_type": "identifier", "index": 0},
            "class": {"child_type": "identifier", "index": 0},
        },
        "docstring": {
            "type": "expression_statement",
            "child_type": "string",
            "position": "first_child_of_body",
        },
    },

    # ─── JavaScript ───────────────────────────────────────
    "javascript": {
        "extensions": [".js", ".mjs", ".cjs"],
        "grammar": "tree_sitter_javascript",
        "node_types": {
            "function": [
                "function_declaration",
                "arrow_function",
                "method_definition",
                "generator_function_declaration",
            ],
            "class": ["class_declaration"],
            "import": ["import_statement"],
            "call": ["call_expression"],
        },
        "test_patterns": {
            "file": ["*.test.js", "*.spec.js", "test_*.js"],
            "function_name": ["describe", "it", "test"],
        },
        "name_extraction": {
            "function": {"child_type": "identifier", "index": 0},
            "class": {"child_type": "identifier", "index": 0},
        },
    },

    # ─── TypeScript ───────────────────────────────────────
    "typescript": {
        "extensions": [".ts"],
        "grammar": "tree_sitter_typescript",
        "node_types": {
            "function": [
                "function_declaration",
                "arrow_function",
                "method_definition",
            ],
            "class": ["class_declaration", "interface_declaration"],
            "import": ["import_statement"],
            "call": ["call_expression"],
        },
        "test_patterns": {
            "file": ["*.test.ts", "*.spec.ts"],
            "function_name": ["describe", "it", "test"],
        },
        "name_extraction": {
            "function": {"child_type": "identifier", "index": 0},
            "class": {"child_type": "type_identifier", "index": 0},
        },
    },

    # ─── TSX ──────────────────────────────────────────────
    "tsx": {
        "extensions": [".tsx"],
        "grammar": "tree_sitter_tsx",
        "node_types": {
            "function": [
                "function_declaration",
                "arrow_function",
                "method_definition",
            ],
            "class": ["class_declaration", "interface_declaration"],
            "import": ["import_statement"],
            "call": ["call_expression"],
        },
        "test_patterns": {
            "file": ["*.test.tsx", "*.spec.tsx"],
            "function_name": ["describe", "it", "test"],
        },
        "name_extraction": {
            "function": {"child_type": "identifier", "index": 0},
            "class": {"child_type": "type_identifier", "index": 0},
        },
    },

    # ─── Go ───────────────────────────────────────────────
    "go": {
        "extensions": [".go"],
        "grammar": "tree_sitter_go",
        "node_types": {
            "function": ["function_declaration", "method_declaration"],
            "class": ["type_declaration"],
            "import": ["import_declaration"],
            "call": ["call_expression"],
        },
        "test_patterns": {
            "file": ["*_test.go"],
            "function_prefix": ["Test", "Benchmark"],
        },
        "name_extraction": {
            "function": {"child_type": "identifier", "index": 0},
            "class": {
                "child_type": "type_spec",
                "nested": {"child_type": "type_identifier", "index": 0},
            },
        },
    },

    # ─── Rust ─────────────────────────────────────────────
    "rust": {
        "extensions": [".rs"],
        "grammar": "tree_sitter_rust",
        "node_types": {
            "function": ["function_item"],
            "class": ["struct_item", "impl_item", "enum_item", "trait_item"],
            "import": ["use_declaration"],
            "call": ["call_expression"],
        },
        "test_patterns": {
            "attribute": ["test"],
            "module_name": ["tests"],
        },
        "name_extraction": {
            "function": {"child_type": "identifier", "index": 0},
            "class": {"child_type": "type_identifier", "index": 0},
        },
    },

    # ─── Java ─────────────────────────────────────────────
    "java": {
        "extensions": [".java"],
        "grammar": "tree_sitter_java",
        "node_types": {
            "function": ["method_declaration", "constructor_declaration"],
            "class": ["class_declaration", "interface_declaration", "enum_declaration"],
            "import": ["import_declaration"],
            "call": ["method_invocation"],
        },
        "test_patterns": {
            "file": ["*Test.java", "*Tests.java"],
            "annotation": ["Test", "ParameterizedTest"],
        },
        "name_extraction": {
            "function": {"child_type": "identifier", "index": 0},
            "class": {"child_type": "identifier", "index": 0},
        },
    },

    # ─── C# ───────────────────────────────────────────────
    "csharp": {
        "extensions": [".cs"],
        "grammar": "tree_sitter_c_sharp",
        "node_types": {
            "function": ["method_declaration", "constructor_declaration"],
            "class": ["class_declaration", "interface_declaration", "struct_declaration"],
            "import": ["using_directive"],
            "call": ["invocation_expression"],
        },
        "test_patterns": {
            "annotation": ["Test", "Fact", "Theory", "TestMethod"],
        },
        "name_extraction": {
            "function": {"child_type": "identifier", "index": 0},
            "class": {"child_type": "identifier", "index": 0},
        },
    },

    # ─── Ruby ─────────────────────────────────────────────
    "ruby": {
        "extensions": [".rb"],
        "grammar": "tree_sitter_ruby",
        "node_types": {
            "function": ["method", "singleton_method"],
            "class": ["class", "module"],
            "import": ["call"],  # require/require_relative are calls in Ruby
            "call": ["call", "command_call"],
        },
        "test_patterns": {
            "file": ["*_test.rb", "test_*.rb", "*_spec.rb"],
            "function_name": ["describe", "it", "context"],
        },
        "name_extraction": {
            "function": {"child_type": "identifier", "index": 0},
            "class": {"child_type": "constant", "index": 0},
        },
        "import_filter": {
            "function_names": ["require", "require_relative"],
        },
    },

    # ─── Kotlin ───────────────────────────────────────────
    "kotlin": {
        "extensions": [".kt", ".kts"],
        "grammar": "tree_sitter_kotlin",
        "node_types": {
            "function": ["function_declaration"],
            "class": ["class_declaration", "object_declaration"],
            "import": ["import_header"],
            "call": ["call_expression"],
        },
        "test_patterns": {
            "annotation": ["Test"],
        },
        "name_extraction": {
            "function": {"child_type": "simple_identifier", "index": 0},
            "class": {"child_type": "type_identifier", "index": 0},
        },
    },

    # ─── Swift ────────────────────────────────────────────
    "swift": {
        "extensions": [".swift"],
        "grammar": "tree_sitter_swift",
        "node_types": {
            "function": ["function_declaration"],
            "class": ["class_declaration", "struct_declaration", "protocol_declaration"],
            "import": ["import_declaration"],
            "call": ["call_expression"],
        },
        "test_patterns": {
            "function_prefix": ["test"],
            "class_suffix": ["Tests", "Test"],
        },
        "name_extraction": {
            "function": {"child_type": "simple_identifier", "index": 0},
            "class": {"child_type": "type_identifier", "index": 0},
        },
    },

    # ─── PHP ──────────────────────────────────────────────
    "php": {
        "extensions": [".php"],
        "grammar": "tree_sitter_php",
        "node_types": {
            "function": ["function_definition", "method_declaration"],
            "class": ["class_declaration", "interface_declaration", "trait_declaration"],
            "import": ["use_declaration", "namespace_use_declaration"],
            "call": ["function_call_expression", "member_call_expression"],
        },
        "test_patterns": {
            "file": ["*Test.php"],
            "function_prefix": ["test"],
        },
        "name_extraction": {
            "function": {"child_type": "name", "index": 0},
            "class": {"child_type": "name", "index": 0},
        },
    },

    # ─── Solidity ─────────────────────────────────────────
    "solidity": {
        "extensions": [".sol"],
        "grammar": "tree_sitter_solidity",
        "node_types": {
            "function": ["function_definition"],
            "class": ["contract_declaration", "interface_declaration", "library_declaration"],
            "import": ["import_directive"],
            "call": ["call_expression"],
        },
        "test_patterns": {
            "file": ["*.t.sol"],
            "function_prefix": ["test"],
        },
        "name_extraction": {
            "function": {"child_type": "identifier", "index": 0},
            "class": {"child_type": "identifier", "index": 0},
        },
    },

    # ─── C ────────────────────────────────────────────────
    "c": {
        "extensions": [".c", ".h"],
        "grammar": "tree_sitter_c",
        "node_types": {
            "function": ["function_definition"],
            "class": ["struct_specifier", "enum_specifier", "union_specifier"],
            "import": ["preproc_include"],
            "call": ["call_expression"],
        },
        "test_patterns": {
            "file": ["test_*.c", "*_test.c"],
            "function_prefix": ["test_"],
        },
        "name_extraction": {
            "function": {"child_type": "function_declarator", "nested": {"child_type": "identifier", "index": 0}},
            "class": {"child_type": "type_identifier", "index": 0},
        },
    },

    # ─── C++ ──────────────────────────────────────────────
    "cpp": {
        "extensions": [".cpp", ".cc", ".cxx", ".hpp", ".hxx"],
        "grammar": "tree_sitter_cpp",
        "node_types": {
            "function": ["function_definition"],
            "class": ["class_specifier", "struct_specifier"],
            "import": ["preproc_include"],
            "call": ["call_expression"],
        },
        "test_patterns": {
            "function_name": ["TEST", "TEST_F", "TEST_P"],
        },
        "name_extraction": {
            "function": {"child_type": "function_declarator", "nested": {"child_type": "identifier", "index": 0}},
            "class": {"child_type": "type_identifier", "index": 0},
        },
    },

    # ─── Dart ─────────────────────────────────────────────
    "dart": {
        "extensions": [".dart"],
        "grammar": "tree_sitter_dart",
        "node_types": {
            "function": ["function_signature", "method_signature"],
            "class": ["class_definition"],
            "import": ["import_or_export"],
            "call": ["call_expression"],
        },
        "test_patterns": {
            "file": ["*_test.dart"],
            "function_name": ["test", "group"],
        },
        "name_extraction": {
            "function": {"child_type": "identifier", "index": 0},
            "class": {"child_type": "identifier", "index": 0},
        },
    },

    # ─── Scala ────────────────────────────────────────────
    "scala": {
        "extensions": [".scala", ".sc"],
        "grammar": "tree_sitter_scala",
        "node_types": {
            "function": ["function_definition"],
            "class": ["class_definition", "object_definition", "trait_definition"],
            "import": ["import_declaration"],
            "call": ["call_expression"],
        },
        "test_patterns": {
            "class_suffix": ["Spec", "Test", "Suite"],
        },
        "name_extraction": {
            "function": {"child_type": "identifier", "index": 0},
            "class": {"child_type": "identifier", "index": 0},
        },
    },

    # ─── R ────────────────────────────────────────────────
    "r": {
        "extensions": [".R", ".r"],
        "grammar": "tree_sitter_r",
        "node_types": {
            "function": ["function_definition"],
            "class": [],  # R uses S3/S4/R6 — detected via patterns
            "import": ["call"],  # library() and require() are calls
            "call": ["call"],
        },
        "test_patterns": {
            "file": ["test-*.R", "test_*.R"],
            "function_name": ["test_that", "expect_equal"],
        },
        "import_filter": {
            "function_names": ["library", "require", "source"],
        },
    },

    # ─── Perl ─────────────────────────────────────────────
    "perl": {
        "extensions": [".pl", ".pm", ".xs"],
        "grammar": "tree_sitter_perl",
        "node_types": {
            "function": ["function_definition"],
            "class": [],  # Perl uses package-as-class
            "import": ["use_statement"],
            "call": ["call"],
        },
        "test_patterns": {
            "file": ["*.t"],
            "function_name": ["ok", "is", "like", "done_testing"],
        },
    },

    # ─── Lua ──────────────────────────────────────────────
    "lua": {
        "extensions": [".lua"],
        "grammar": "tree_sitter_lua",
        "node_types": {
            "function": ["function_declaration", "local_function"],
            "class": [],  # Lua uses table-as-class pattern
            "import": [],  # require() is a call
            "call": ["function_call"],
        },
        "test_patterns": {
            "file": ["*_spec.lua", "test_*.lua"],
            "function_name": ["describe", "it"],
        },
        "import_filter": {
            "function_names": ["require", "dofile"],
        },
    },

    # ─── Vue ──────────────────────────────────────────────
    "vue": {
        "extensions": [".vue"],
        "grammar": "tree_sitter_vue",
        "node_types": {
            "function": [
                "function_declaration",
                "arrow_function",
                "method_definition",
            ],
            "class": ["class_declaration"],
            "import": ["import_statement"],
            "call": ["call_expression"],
        },
        "test_patterns": {
            "file": ["*.test.vue", "*.spec.vue"],
        },
        "script_extraction": True,  # Parse <script> tag content
    },
}


def get_language_for_file(filepath: str) -> str | None:
    """Determine the language of a file based on its extension.
    
    Args:
        filepath: Path to the file
        
    Returns:
        Language key string, or None if unsupported
    """
    import os
    ext = os.path.splitext(filepath)[1].lower()
    for lang, config in LANGUAGE_CONFIGS.items():
        if ext in [e.lower() for e in config["extensions"]]:
            return lang
    return None


def get_supported_extensions() -> set:
    """Return all file extensions we can parse."""
    extensions = set()
    for config in LANGUAGE_CONFIGS.values():
        extensions.update(config["extensions"])
    return extensions
