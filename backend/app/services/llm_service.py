import os

from dotenv import load_dotenv
import cohere

from app.observability.instrumentation import measure_model_call


load_dotenv()


api_key = os.getenv("COHERE_API_KEY")

if not api_key:
    raise ValueError(
        "COHERE_API_KEY is not set in the .env file."
    )


client = cohere.ClientV2(
    api_key=api_key
)


@measure_model_call("chat")
def generate_answer(
    question: str,
    context: str
) -> str:

    prompt = f"""
You are an AI Business Process Assistant.

Answer the user's question using ONLY the
provided business document context.

Do not invent information.

If the answer cannot be found in the
provided context, say:

"I could not find this information in the
provided documents."

Business document context:
---------------------------
{context}
---------------------------

User question:
{question}
"""

    response = client.chat(
        model="command-a-03-2025",
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    return response.message.content[0].text