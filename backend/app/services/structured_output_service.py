import json
import logging

from app.observability.instrumentation import measure_model_call
from app.services.llm_service import client


logger = logging.getLogger(__name__)


@measure_model_call("structured_output")
def extract_business_rule(
    question: str,
    context: str
) -> dict:

    prompt = f"""
You are an AI Business Process Analysis Agent.

Analyze the provided business document context
and extract the business rule relevant to the user's question.

Return ONLY a valid JSON object.
Do NOT use markdown.
Do NOT use ```json.
Do NOT add explanations before or after the JSON.

Required format:

{{
    "process": "",
    "rule": "",
    "threshold": null,
    "currency": "",
    "approval_required": false,
    "approver": "",
    "evidence": ""
}}

Rules:

1. Use ONLY information present in the context.
2. Do not invent information.
3. If a threshold is not mentioned, use null.
4. If currency is not mentioned, use an empty string.
5. If approval is not mentioned, use false.
6. If the approver is not mentioned, use an empty string.
7. Put the supporting sentence in "evidence".

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

    content = response.message.content[0].text.strip()

    # Debug: see exactly what Cohere returned
    logger.debug("Raw Cohere response: %s", content)

    # Remove Markdown code fences if the model still adds them
    if content.startswith("```"):
        lines = content.splitlines()

        if lines[0].startswith("```"):
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        content = "\n".join(lines).strip()

    try:
        return json.loads(content)

    except json.JSONDecodeError:
        logger.error(
            "Cohere did not return valid JSON. Returned content: %s",
            content
        )

        raise ValueError(
            "Cohere returned an invalid JSON response."
        )