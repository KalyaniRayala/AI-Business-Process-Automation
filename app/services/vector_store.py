import chromadb

from app.services.embedding_services import create_embeddings


# ============================================================
# CHROMADB CLIENT
# ============================================================

CHROMA_PATH = "chroma_db"

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

    collection.add(
        ids=ids,
        documents=texts,
        embeddings=embeddings,
        metadatas=metadatas
    )

    print(f"Added {len(records)} chunks to ChromaDB.")


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