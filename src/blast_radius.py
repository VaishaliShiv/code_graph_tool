"""
Blast Radius Engine.

Given a changed file or function, traces every caller, dependent, and test
that could be affected — the "blast radius" of the change.

This is pure graph traversal (no AI). It walks edges in the graph database
to find everything connected to the change.

Usage:
    analyzer = BlastRadiusAnalyzer(db)
    result = analyzer.analyze_file("auth/service.py")
    result = analyzer.analyze_function("login")
"""

from collections import defaultdict


class BlastRadiusAnalyzer:
    """Traces the impact of code changes through the dependency graph."""
    
    def __init__(self, db):
        self.db = db
    
    def analyze_file(self, filepath: str, max_depth: int = 3) -> dict:
        """
        Compute the blast radius for a changed file.
        
        Traces:
        1. All functions defined in the file
        2. All callers of those functions (direct impact)
        3. All callers of the callers (indirect impact, up to max_depth)
        4. All tests that cover affected functions
        5. All files that import from this file
        
        Args:
            filepath: Relative path to the changed file
            max_depth: How many levels of callers to trace
            
        Returns:
            dict with affected functions, files, tests, and risk scores
        """
        # Get all nodes in the changed file
        changed_nodes = self.db.get_nodes_by_file(filepath)
        if not changed_nodes:
            return {
                "changed_file": filepath,
                "error": f"No nodes found for {filepath}",
                "affected_files": [],
                "affected_functions": [],
                "tests": {"covered": [], "missing": []},
                "risk_score": 0,
            }
        
        changed_func_names = [n["name"] for n in changed_nodes if n["type"] == "function"]
        
        # Trace the blast radius
        directly_affected = []
        indirectly_affected = []
        all_affected_files = {filepath}
        visited = set()
        
        # Level 1: Direct callers
        for func_name in changed_func_names:
            callers = self.db.get_callers(func_name)
            for caller in callers:
                if caller["from_name"] and caller["from_name"] not in visited:
                    visited.add(caller["from_name"])
                    directly_affected.append({
                        "function": caller["from_name"],
                        "file": caller["file"] or "unknown",
                        "reason": f"directly calls {func_name}",
                        "risk": "high",
                    })
                    if caller["file"]:
                        all_affected_files.add(caller["file"])
        
        # Level 2+: Indirect callers
        frontier = [item["function"] for item in directly_affected]
        for depth in range(2, max_depth + 1):
            next_frontier = []
            for func_name in frontier:
                callers = self.db.get_callers(func_name)
                for caller in callers:
                    if caller["from_name"] and caller["from_name"] not in visited:
                        visited.add(caller["from_name"])
                        indirectly_affected.append({
                            "function": caller["from_name"],
                            "file": caller["file"] or "unknown",
                            "reason": f"indirectly affected (depth {depth}) via {func_name}",
                            "risk": "medium" if depth == 2 else "low",
                        })
                        next_frontier.append(caller["from_name"])
                        if caller["file"]:
                            all_affected_files.add(caller["file"])
            frontier = next_frontier
        
        # Find tests
        covered_tests = []
        all_tested_funcs = set()
        for func_name in changed_func_names:
            tests = self.db.get_tests_for(func_name)
            for test in tests:
                covered_tests.append({
                    "test_name": test["test_name"],
                    "test_file": test["test_file"] or "unknown",
                    "covers": func_name,
                })
                all_tested_funcs.add(func_name)
                if test["test_file"]:
                    all_affected_files.add(test["test_file"])
        
        # Find untested functions
        missing_tests = [
            f for f in changed_func_names 
            if f not in all_tested_funcs and not f.startswith("_")
        ]
        
        # File-level dependents
        file_dependents = self.db.get_dependents(filepath)
        for dep in file_dependents:
            if dep["file"] and dep["file"] not in all_affected_files:
                all_affected_files.add(dep["file"])
        
        # Risk score: higher = more dangerous change
        risk_score = (
            len(directly_affected) * 3 +
            len(indirectly_affected) * 1 +
            len(missing_tests) * 5 +  # Untested changes are risky
            max(0, len(changed_func_names) - 1) * 2
        )
        
        return {
            "changed_file": filepath,
            "changed_functions": changed_func_names,
            "directly_affected": directly_affected,
            "indirectly_affected": indirectly_affected,
            "affected_files": sorted(all_affected_files),
            "affected_file_count": len(all_affected_files),
            "tests": {
                "covered": covered_tests,
                "missing": missing_tests,
            },
            "risk_score": min(risk_score, 100),
            "risk_level": (
                "critical" if risk_score > 50 else
                "high" if risk_score > 30 else
                "medium" if risk_score > 15 else
                "low"
            ),
            "review_order": self._suggest_review_order(
                filepath, directly_affected, indirectly_affected, covered_tests
            ),
        }
    
    def analyze_function(self, func_name: str) -> dict:
        """Compute blast radius for a specific function."""
        callers = self.db.get_callers(func_name)
        callees = self.db.get_callees(func_name)
        tests = self.db.get_tests_for(func_name)
        
        return {
            "function": func_name,
            "callers": callers,
            "callees": callees,
            "tests": tests,
            "caller_count": len(callers),
            "callee_count": len(callees),
            "test_count": len(tests),
            "has_tests": len(tests) > 0,
        }
    
    def compare_with_without_graph(self, filepath: str, total_project_files: int) -> dict:
        """
        Compare token usage WITH and WITHOUT the graph.
        
        Without graph: AI reads ALL files
        With graph: AI reads only blast radius files
        
        Returns a comparison dict for the dashboard.
        """
        blast = self.analyze_file(filepath)

        # Estimate tokens (~800 tokens per source file average)
        tokens_per_file = 800

        # Cap: blast can never logically exceed total files
        # (path format mismatches can inflate the count)
        affected = min(blast["affected_file_count"], total_project_files)
        full_codebase_impact = affected >= total_project_files

        without_graph = {
            "files_read": total_project_files,
            "estimated_tokens": total_project_files * tokens_per_file,
            "approach": "Read entire codebase",
        }

        with_graph = {
            "files_read": affected,
            "estimated_tokens": affected * tokens_per_file,
            "approach": "Read only blast radius",
            "full_codebase_impact": full_codebase_impact,
        }

        # Reduction is always >= 1.0x  (with graph never costs more)
        reduction = max(
            1.0,
            without_graph["estimated_tokens"] / max(with_graph["estimated_tokens"], 1)
        )

        return {
            "without_graph": without_graph,
            "with_graph": with_graph,
            "token_reduction": f"{reduction:.1f}x",
            "files_saved": total_project_files - affected,
            "full_codebase_impact": full_codebase_impact,
            "blast_radius": blast,
        }
    
    def _suggest_review_order(self, changed_file, direct, indirect, tests):
        """Suggest the order in which files should be reviewed."""
        order = []
        order.append(f"1. {changed_file} (the change)")
        
        seen_files = {changed_file}
        step = 2
        for item in direct:
            if item["file"] not in seen_files:
                order.append(f"{step}. {item['file']} (direct dependent — {item['reason']})")
                seen_files.add(item["file"])
                step += 1
        
        test_files = set()
        for t in tests:
            if t["test_file"] not in seen_files:
                test_files.add(t["test_file"])
        for tf in sorted(test_files):
            order.append(f"{step}. {tf} (test file)")
            step += 1
        
        for item in indirect[:5]:  # Cap indirect at 5
            if item["file"] not in seen_files:
                order.append(f"{step}. {item['file']} (indirect — {item['reason']})")
                seen_files.add(item["file"])
                step += 1
        
        return order
