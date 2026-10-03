import logging
import os

import chromadb

from app.services.embedding_services import create_embeddings


logger = logging.getLogger(__name__)


# ============================================================
# CHROMADB CLIENT
# ============================================================

# Override only for test isolation; defaults to the project store.
CHROMA_PATH = os.getenv("CHROMA_PATH", "chroma_db")

client = chromadb.PersistentClient(
    path=CHROMA_PATH
)


# ============================================================
# COLLECTION
# ============================================================

collection = client.get_or_create_collection(
    name="business_process_documents"
)


# ============================================================
# ADD DOCUMENT CHUNKS
# ============================================================

def add_documents(records: list[dict]) -> None:
    """
    Store document chunks and their embeddings in ChromaDB.
    """

    if not records:
        return

    texts = [
        record["text"]
        for record in records
    ]

    embeddings = create_embeddings(
        texts,
        input_type="search_document"
    )

    ids = [
        f"{record['source']}_{record['chunk_id']}"
        for record in records
    ]

    metadatas = [
        {
            "source": record["source"],
            "chunk_id": record["chunk_id"]
        }
        for record in records
    ]

    # Upsert so re-ingesting the same document updates its
    # chunks instead of skipping duplicate IDs.
    collection.upsert(
        ids=ids,
        documents=texts,
        embeddings=embeddings,
        metadatas=metadatas
    )

    logger.info(
        "Upserted %d chunks to ChromaDB.",
        len(records)
    )


# ============================================================
# SEARCH DOCUMENTS
# ============================================================

def search_documents(
    query: str,
    top_k: int = 3
) -> dict:
    """
    Search ChromaDB using semantic similarity.
    """

    query_embedding = create_embeddings(
        [query],
        input_type="search_query"
    )[0]

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k
    )

    return results


# ============================================================
# COLLECTION INFORMATION
# ============================================================

def get_collection_count() -> int:
    """
    Return the number of stored chunks.
    """

    return collection.count()