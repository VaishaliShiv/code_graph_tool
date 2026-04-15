"""
Embedding Engine for Semantic Search.

Generates vector embeddings for code entities using open-source models.
Supports multiple backends:
  - sentence-transformers (all-MiniLM-L6-v2) — recommended, fully offline
  - numpy-only fallback (TF-IDF style) — no ML dependencies needed

Usage:
    engine = EmbeddingEngine()
    engine.embed_nodes(db)  # Embeds all nodes in the graph DB
    results = engine.semantic_search(db, "authentication logic")
"""

import struct
import math
import re
from collections import Counter
from typing import Optional


def _vector_to_bytes(vector: list[float]) -> bytes:
    """Pack a float vector into bytes for SQLite storage."""
    return struct.pack(f'{len(vector)}f', *vector)


def _bytes_to_vector(data: bytes) -> list[float]:
    """Unpack bytes back into a float vector."""
    n = len(data) // 4
    return list(struct.unpack(f'{n}f', data))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _build_embedding_text(node: dict, graph_ctx: dict | None = None) -> str:
    """Build the text string embedded for a node.

    Combines static node fields AND live graph neighbourhood.
    The call graph is the richest semantic signal for code — a function
    with no docstring but calls verify_token + hash_password is clearly
    auth-related. Including callers/callees/tests encodes that meaning.

    Example output:
        "login | def login(self, email: str, password: str) -> dict |
         Authenticate user | File: auth/service.py | Class: AuthService |
         calls: verify_token hash_password get_user |
         callers: handle_login api_login | tests: test_login"
    """
    parts = [node.get("name", "")]

    sig = node.get("signature", "")
    if sig:
        parts.append(sig)

    doc = node.get("docstring", "")
    if doc:
        parts.append(doc)

    body = node.get("body_preview", "")
    if body:
        parts.append(body[:300])

    file = node.get("file", "")
    if file:
        parts.append(f"File: {file}")

    parent = node.get("parent_class", "")
    if parent:
        parts.append(f"Class: {parent}")

    node_type = node.get("type", "")
    if node_type:
        parts.append(f"Type: {node_type}")

    # -- Graph neighbourhood (the key architectural improvement) --
    if graph_ctx:
        callees = graph_ctx.get("callees", [])
        if callees:
            parts.append("calls: " + " ".join(callees[:8]))

        callers = graph_ctx.get("callers", [])
        caller_names = [c["name"] if isinstance(c, dict) else c for c in callers[:6]]
        if caller_names:
            parts.append("callers: " + " ".join(caller_names))

        tests = graph_ctx.get("tests", [])
        if tests:
            parts.append("tests: " + " ".join(tests[:4]))

    return " | ".join(parts)


class EmbeddingEngine:
    """
    Generates and searches vector embeddings for code entities.
    
    Recommended model: all-MiniLM-L6-v2 (sentence-transformers)
    - 80MB download, runs on any laptop CPU
    - 384-dimensional vectors
    - Great quality for code + natural language
    - Fully offline, no API keys
    
    Falls back to TF-IDF if sentence-transformers is not installed.
    """
    
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self.model = None
        self._use_transformer = False
        self._vocab = {}  # For TF-IDF fallback
        self._idf = {}
        self._dim = 384
        
        # Try to load sentence-transformers
        try:
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(model_name)
            self._use_transformer = True
            print(f"[embeddings] Loaded {model_name} (sentence-transformers)")
        except ImportError:
            print(f"[embeddings] sentence-transformers not installed.")
            print(f"[embeddings] Using TF-IDF fallback (install sentence-transformers for better results)")
            print(f"[embeddings] pip install sentence-transformers")
    
    def _tokenize(self, text: str) -> list[str]:
        """Simple tokenizer for TF-IDF fallback."""
        text = text.lower()
        text = re.sub(r'[^a-z0-9_]', ' ', text)
        # Split camelCase and snake_case
        text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
        text = text.replace('_', ' ')
        tokens = text.split()
        return [t for t in tokens if len(t) > 1]
    
    def _build_tfidf_vocab(self, texts: list[str]):
        """Build vocabulary and IDF weights from a corpus."""
        doc_count = len(texts)
        doc_freq = Counter()
        
        for text in texts:
            tokens = set(self._tokenize(text))
            for token in tokens:
                doc_freq[token] += 1
        
        # Top N terms by document frequency
        top_terms = doc_freq.most_common(self._dim)
        self._vocab = {term: idx for idx, (term, _) in enumerate(top_terms)}
        self._idf = {
            term: math.log(doc_count / (1 + freq))
            for term, freq in doc_freq.items()
            if term in self._vocab
        }
    
    def _tfidf_embed(self, text: str) -> list[float]:
        """Generate a TF-IDF vector for a text string."""
        tokens = self._tokenize(text)
        tf = Counter(tokens)
        
        vector = [0.0] * self._dim
        for token, count in tf.items():
            if token in self._vocab:
                idx = self._vocab[token]
                idf = self._idf.get(token, 1.0)
                vector[idx] = count * idf
        
        # L2 normalize
        norm = math.sqrt(sum(x * x for x in vector))
        if norm > 0:
            vector = [x / norm for x in vector]
        
        return vector
    
    def embed_text(self, text: str) -> list[float]:
        """Generate an embedding vector for a text string."""
        if self._use_transformer and self.model:
            vec = self.model.encode(text, show_progress_bar=False)
            return vec.tolist()
        else:
            return self._tfidf_embed(text)
    
    def embed_nodes(self, db, batch_size: int = 64):
        """
        Embed all nodes in the graph database.
        
        For each node, builds a rich text string from name + signature + 
        docstring + file path, then generates a vector embedding.
        
        Args:
            db: GraphDB instance
            batch_size: Number of nodes to embed at once (for transformer)
        """
        nodes = db.get_all_nodes()

        if not nodes:
            print("[embeddings] No nodes to embed")
            return

        # Build graph-enriched text for each node.
        # Fetching graph context per node adds a few extra SQLite queries
        # but pays off in much better semantic search quality — the call
        # graph neighbourhood is the richest signal we have for code meaning.
        texts = []
        node_ids = []
        for node in nodes:
            ctx = db.get_graph_context(node["name"]) if node.get("type") == "function" else None
            text = _build_embedding_text(node, graph_ctx=ctx)
            texts.append(text)
            node_ids.append(node["id"])
        
        print(f"[embeddings] Embedding {len(texts)} nodes...")
        
        if self._use_transformer and self.model:
            # Batch encode with sentence-transformers
            vectors = self.model.encode(texts, show_progress_bar=True, batch_size=batch_size)
            for node_id, vector, text in zip(node_ids, vectors, texts):
                db.store_embedding(
                    node_id=node_id,
                    vector=_vector_to_bytes(vector.tolist()),
                    text_input=text,
                    model=self.model_name,
                )
        else:
            # TF-IDF fallback
            self._build_tfidf_vocab(texts)
            for node_id, text in zip(node_ids, texts):
                vector = self._tfidf_embed(text)
                db.store_embedding(
                    node_id=node_id,
                    vector=_vector_to_bytes(vector),
                    text_input=text,
                    model="tfidf-fallback",
                )
        
        print(f"[embeddings] Done — {len(texts)} nodes embedded")
    
    def semantic_search(self, db, query: str, top_k: int = 5) -> list[dict]:
        """
        Search for nodes by meaning using vector similarity.
        
        Args:
            db: GraphDB instance
            query: Natural language search query (e.g., "authentication logic")
            top_k: Number of results to return
            
        Returns:
            List of dicts with node info + similarity score
        """
        query_vector = self.embed_text(query)
        
        all_embeddings = db.get_all_embeddings()
        
        results = []
        for emb in all_embeddings:
            stored_vector = _bytes_to_vector(emb["vector"])
            similarity = _cosine_similarity(query_vector, stored_vector)
            results.append({
                "node_id": emb["node_id"],
                "name": emb["name"],
                "type": emb["type"],
                "file": emb["file"],
                "signature": emb["signature"],
                "docstring": emb["docstring"],
                "is_test": emb.get("is_test", 0),
                "similarity": round(similarity, 4),
                "text_embedded": emb["text_input"],
            })
        
        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:top_k]


class CrossEncoderReranker:
    """Re-ranks retrieval candidates using a cross-encoder model.

    A bi-encoder (like all-MiniLM) embeds query and document independently
    and compares vectors — fast but imprecise. A cross-encoder sees
    (query, document) together and scores their relevance jointly — slower
    but significantly more accurate.

    Pipeline:
        retrieve top-20 (fast, bi-encoder / FTS5)
        → re-rank to top-5 (cross-encoder)
        → return

    Model: cross-encoder/ms-marco-MiniLM-L-2-v2
        - ~80MB, CPU-fast (same footprint as all-MiniLM)
        - trained on MS MARCO passage ranking
        - works well for code because it understands function names + signatures

    Falls back gracefully to no-op if sentence-transformers not installed.
    """

    _DEFAULT_MODEL = "cross-encoder/ms-marco-MiniLM-L-2-v2"

    def __init__(self, model_name: str = _DEFAULT_MODEL):
        self.model = None
        self._available = False
        try:
            from sentence_transformers import CrossEncoder
            self.model = CrossEncoder(model_name)
            self._available = True
            print(f"[reranker] Loaded {model_name}")
        except ImportError:
            pass  # sentence-transformers not installed — skip silently
        except Exception as e:
            print(f"[reranker] Could not load {model_name}: {e}")

    def rerank(self, query: str, results: list[dict], top_k: int = 5) -> list[dict]:
        """Re-rank results by (query, document) joint relevance score.

        Two safety nets:
        1. If all cross-encoder scores are negative the model has no signal
           (common for short stub functions) — fall back to input order so
           the reranker doesn't make things worse.
        2. Test penalty: when query has no test intent, demote test nodes
           AFTER cross-encoder scoring so they can't override the penalty
           that was applied during retrieval.
        """
        if not self._available or not results or len(results) <= 1:
            return results[:top_k]

        pairs = []
        for r in results:
            graph = r.get("graph") or {}
            callers = graph.get("callers", [])
            calls = graph.get("calls", [])
            callers_str = "callers: " + " ".join(callers[:5]) if callers else ""
            calls_str = "calls: " + " ".join(calls[:5]) if calls else ""
            doc = " ".join(filter(None, [
                r.get("name", ""),
                r.get("signature", ""),
                r.get("docstring", ""),
                r.get("file", ""),
                callers_str,
                calls_str,
            ]))
            pairs.append((query, doc))

        raw_scores = self.model.predict(pairs)

        # Safety net: if no result scores positively, the model has no useful
        # signal — return input order (already ranked by hybrid/keyword) unchanged.
        if max(float(s) for s in raw_scores) < 0:
            return results[:top_k]

        query_wants_tests = any(
            w in query.lower() for w in ("test", "spec", "fixture", "mock")
        )

        adjusted = []
        for score, r in zip(raw_scores, results):
            s = float(score)
            # Apply test penalty post-rerank so it isn't overridden
            if not query_wants_tests and r.get("is_test"):
                s *= 0.5
            adjusted.append((s, r))

        ranked = sorted(adjusted, key=lambda x: x[0], reverse=True)
        reranked = []
        for score, r in ranked[:top_k]:
            r = dict(r)
            r["rerank_score"] = round(score, 4)
            reranked.append(r)
        return reranked


def detect_query_mode(query: str) -> str:
    """Auto-detect best search mode from query structure.

    - Pure code identifier (snake_case / PascalCase / camelCase) → keyword
      Exact name match is faster and more precise than semantic.
    - Natural language (spaces, multiple words, no underscores) → hybrid
      Needs semantic to find conceptually related code.
    """
    import re
    q = query.strip()
    # Single token that looks like a code identifier
    if ' ' not in q:
        if re.match(r'^[a-z][a-z0-9]*(_[a-z0-9]+)+$', q):          # snake_case: needs underscore
            return "keyword"
        if re.match(r'^[A-Z][a-z0-9]+([A-Z][a-z0-9]*)+$', q):    # PascalCase: multiple words
            return "keyword"
        if re.match(r'^[a-z][a-z0-9]*([A-Z][a-z0-9]*)+$', q):    # camelCase: has uppercase
            return "keyword"
    return "hybrid"


def hybrid_search(db, engine: EmbeddingEngine, query: str, top_k: int = 5,
                  keyword_weight: float = 0.4, semantic_weight: float = 0.6,
                  score_threshold: float = 0.1) -> list[dict]:
    """Hybrid search: FTS5 keyword + vector semantic, properly score-normalized.

    Keyword and semantic scores live on different scales, so each is
    min-max normalized to [0, 1] before weighting. This prevents one
    signal drowning the other.

    Auto-adjusts weights:
    - If only keyword results exist → keyword_weight = 1.0
    - If only semantic results exist → semantic_weight = 1.0
    """
    keyword_results = db.keyword_search(query, limit=top_k * 2)
    semantic_results = engine.semantic_search(db, query, top_k=top_k * 2)

    scores: dict[str, float] = {}
    node_info: dict[str, dict] = {}

    # Detect test intent: if query mentions "test" we don't penalise test nodes
    query_wants_tests = any(w in query.lower() for w in ("test", "spec", "fixture", "mock"))

    # -- Keyword scores: normalize rank to [0, 1] via reciprocal rank --
    for i, kr in enumerate(keyword_results):
        node_id = kr["id"]
        kw_score = 1.0 / (1.0 + i)
        scores[node_id] = scores.get(node_id, 0.0) + keyword_weight * kw_score
        node_info[node_id] = {
            "name": kr["name"], "type": kr["type"], "file": kr["file"],
            "signature": kr.get("signature") or "",
            "docstring": kr.get("docstring") or "",
            "is_test": kr.get("is_test", 0),
        }

    # -- Semantic scores: min-max normalize within result set so top = 1.0 --
    if semantic_results:
        max_sim = max(sr["similarity"] for sr in semantic_results)
        min_sim = min(sr["similarity"] for sr in semantic_results)
        sim_range = max(max_sim - min_sim, 1e-6)
        for sr in semantic_results:
            node_id = sr["node_id"]
            sem_score = (sr["similarity"] - min_sim) / sim_range
            scores[node_id] = scores.get(node_id, 0.0) + semantic_weight * sem_score
            if node_id not in node_info:
                node_info[node_id] = {
                    "name": sr["name"], "type": sr["type"], "file": sr["file"],
                    "signature": sr.get("signature") or "",
                    "docstring": sr.get("docstring") or "",
                    "is_test": sr.get("is_test", 0),
                }

    # -- Test penalty: demote test nodes when query has no test intent --
    # Prevents test_verify_token outranking verify_token for "verify_token" query.
    TEST_PENALTY = 0.65
    if not query_wants_tests:
        for node_id, info in node_info.items():
            if info.get("is_test"):
                scores[node_id] = scores[node_id] * TEST_PENALTY

    # Sort, filter low-relevance noise, cap at top_k
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    ranked = [(nid, s) for nid, s in ranked if s >= score_threshold]

    results = []
    for node_id, score in ranked[:top_k]:
        info = node_info[node_id]
        doc = info.get("docstring") or ""
        if len(doc) > 120:
            doc = doc[:120].rstrip() + "…"
        results.append({
            "node_id": node_id,
            "score": round(score, 4),
            "name": info["name"],
            "type": info["type"],
            "file": info["file"],
            "signature": info.get("signature") or "",
            "docstring": doc,
        })

    return results
