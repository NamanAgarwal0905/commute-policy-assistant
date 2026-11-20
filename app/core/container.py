"""Builds the app's shared objects once. Tests build their own Container with a fake LLM."""
import logging
import os
from functools import lru_cache

from app.core.config import Settings
from app.db.chroma import ChunkStore
from app.db.embeddings import make_embedding_function
from app.db.metadata import MetadataDB
from app.services.answer import AnswerService
from app.services.ingestion import reconcile_staging
from app.services.llm import make_llm
from app.services.retrieval import Retriever

log = logging.getLogger(__name__)


class Container:
    def __init__(self, settings: Settings, llm=None):
        self.settings = settings
        os.makedirs(settings.data_dir, exist_ok=True)
        self.db = MetadataDB(settings.sqlite_path)
        self.store = ChunkStore(settings.chroma_path, make_embedding_function(settings.embedding_provider))
        self.llm = llm or make_llm(settings)
        self.retriever = Retriever(self.store, settings)
        self.answers = AnswerService(self.db, self.retriever, self.llm, settings)
        cleaned = reconcile_staging(self.db, self.store)
        if cleaned:
            log.warning("cleaned up %d interrupted ingestion(s)", cleaned)


@lru_cache
def get_container() -> Container:
    return Container(Settings())
