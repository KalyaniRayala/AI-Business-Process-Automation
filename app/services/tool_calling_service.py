import json

from app.services.llm_service import client

from app.services.tool_service import (
    lookup_expense_policy,
    expense_policy_tool,
    search_business_documents,
    document_search_tool,
    lookup_employee,
    employee_lookup_tool
)


def run_tool_agent(question: str):

    tools = [
        expense_policy_tool,
        document_search_tool,
        employee_lookup_tool
    ]

    messages = [
        {
            "role": "user",
            "content": question
        }
    ]

    response = client.chat(
        model="command-a-03-2025",
        messages=messages,
        tools=tools
    )

    if not response.message.tool_calls:
        return response.message.content[0].text

    tool_results = []

    for tool_call in response.message.tool_calls:

        tool_name = tool_call.function.name

        arguments = json.loads(
            tool_call.function.arguments
        )

        if tool_name == "lookup_expense_policy":

            result = lookup_expense_policy(
                expense_amount=arguments["expense_amount"]
            )

        elif tool_name == "search_business_documents":

            result = search_business_documents(
                query=arguments["query"]
            )

        elif tool_name == "lookup_employee":

            result = lookup_employee(
                employee_id=arguments["employee_id"]
            )

        else:

            result = {
                "error": f"Unknown tool: {tool_name}"
            }

        tool_results.append(
            {
                "call": {
                    "name": tool_name,
                    "parameters": arguments
                },
                "outputs": [result]
            }
        )

    messages.append(response.message)

    for tool_call, tool_result in zip(
        response.message.tool_calls,
        tool_results
    ):

        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(tool_result)
            }
        )

    final_response = client.chat(
        model="command-a-03-2025",
        messages=messages,
        tools=tools
    )

    return final_response.message.content[0].text