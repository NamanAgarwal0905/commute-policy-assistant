"""Embedding functions for Chroma.

- "minilm": Chroma's built-in all-MiniLM-L6-v2 (ONNX, runs locally, free). Used in the demo.
  The model file (~80 MB) is downloaded once; the Dockerfile does that at build time.
- "hash": a tiny offline bag-of-words embedder. It needs no download and is deterministic,
  so unit tests and CI never need the network. It is a TEST STAND-IN, not a quality model.
"""
import hashlib
import math
import re

from chromadb import Documents, EmbeddingFunction, Embeddings
from chromadb.utils.embedding_functions import register_embedding_function

DIMS = 512
STOP = set(
    "a an the i me my is are be am do does can to of in on at for and or if it we you your our what which who "
    "how any there this that will with by as from need get have has should must may use about tell please "
    "would like want".split()
)
# Words that appear in nearly every policy chunk get a lower weight (a poor man's IDF).
COMMON = {"cab", "travel", "employ", "polic", "trip", "compa", "trans", "ride", "book", "month"}
SYNONYMS = {"ride": "cab", "reserve": "book", "booking": "book", "hour": "hour", "hours": "hour", "visitor": "guest"}


def _tokens(text: str):
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        if tok in STOP:
            continue
        tok = SYNONYMS.get(tok, tok)
        # Crude stemming: first 5 letters ("approves", "approval", "approve" -> "appro").
        yield tok if tok.isdigit() else tok[:5]


@register_embedding_function
class HashEmbedding(EmbeddingFunction[Documents]):
    def __init__(self):
        pass

    def __call__(self, input: Documents) -> Embeddings:
        out = []
        for text in input:
            vec = [0.0] * DIMS
            for tok in _tokens(text):
                idx = int(hashlib.md5(tok.encode()).hexdigest()[:8], 16) % DIMS
                vec[idx] += 0.3 if tok in COMMON else 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out

    @staticmethod
    def name() -> str:
        return "hash-bow"

    def get_config(self):
        return {}

    @staticmethod
    def build_from_config(config):
        return HashEmbedding()


def make_embedding_function(provider: str):
    if provider == "hash":
        return HashEmbedding()
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

    return DefaultEmbeddingFunction()
