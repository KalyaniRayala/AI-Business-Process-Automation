from app.services.rag_service import retrieve_context


query = "Who approves expenses above 500 dollars?"


contexts = retrieve_context(
    query,
    top_k=3
)


print("\n==============================")
print("RAG RETRIEVED CONTEXT")
print("==============================")


for index, context in enumerate(contexts):

    print(f"\n--- CONTEXT {index + 1} ---")

    print(context)