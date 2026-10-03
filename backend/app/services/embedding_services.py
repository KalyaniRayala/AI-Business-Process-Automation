import os

import cohere
from dotenv import load_dotenv

from app.observability.instrumentation import measure_model_call


load_dotenv()


COHERE_API_KEY = os.getenv("COHERE_API_KEY")


if not COHERE_API_KEY:
    raise ValueError(
        "COHERE_API_KEY is not set in the .env file."
    )


co = cohere.ClientV2(
    api_key=COHERE_API_KEY
)


@measure_model_call("embed")
def create_embeddings(
    texts: list[str],
    input_type: str = "search_document"
) -> list[list[float]]:
    """
    Generate Cohere embeddings.

    search_document -> for document chunks
    search_query    -> for user search queries
    """

    if not texts:
        return []

    response = co.embed(
        model="embed-v4.0",
        texts=texts,
        input_type=input_type,
        embedding_types=["float"]
    )

    return response.embeddings.float