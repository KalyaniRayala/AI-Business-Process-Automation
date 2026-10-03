from app.services.document_processor import (
    load_document,
    clean_text,
    create_chunks,
    create_chunk_records
)

from app.services.vector_store import (
    add_documents,
    search_documents,
    get_collection_count
)


# ============================================================
# LOAD DOCUMENT
# ============================================================

file_path = "data/expense_process.txt"

text = load_document(file_path)


# ============================================================
# CLEAN
# ============================================================

cleaned_text = clean_text(text)


# ============================================================
# CHUNK
# ============================================================

chunks = create_chunks(
    cleaned_text,
    chunk_size=100,
    overlap=20
)


# ============================================================
# CREATE RECORDS
# ============================================================

records = create_chunk_records(
    chunks,
    source=file_path
)


# ============================================================
# STORE IN CHROMADB
# ============================================================

add_documents(records)


# ============================================================
# CHECK COUNT
# ============================================================

print("\n==============================")
print("CHROMADB")
print("==============================")

print(
    "Total stored chunks:",
    get_collection_count()
)


# ============================================================
# SEARCH
# ============================================================

query = "Who approves expenses above 500 dollars?"

results = search_documents(
    query,
    top_k=3
)


# ============================================================
# DISPLAY RESULTS
# ============================================================

print("\n==============================")
print("SEARCH RESULTS")
print("==============================")

for i, document in enumerate(results["documents"][0]):

    print(f"\n--- RESULT {i + 1} ---")
    print(document)