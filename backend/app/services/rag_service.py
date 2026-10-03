from app.services.vector_store import search_documents
from app.services.llm_service import generate_answer
from app.services.structured_output_service import (
    extract_business_rule
)
def retrieve_context(
    query: str,
    top_k: int = 3
) -> list[str]:
    """
    Retrieve the most relevant document chunks
    from ChromaDB.
    """

    results = search_documents(
        query=query,
        top_k=top_k
    )

    documents = results.get(
        "documents",
        [[]]
    )[0]

    return documents

def answer_question(
    question: str,
    top_k: int = 3
) -> str:

    contexts = retrieve_context(
        question,
        top_k
    )

    context = "\n\n".join(contexts)

    answer = generate_answer(
        question=question,
        context=context
    )

    return answer
def analyze_business_rule(
    question: str,
    top_k: int = 3
) -> dict:

    contexts = retrieve_context(
        question,
        top_k
    )

    context = "\n\n".join(contexts)

    result = extract_business_rule(
        question=question,
        context=context
    )

    return result