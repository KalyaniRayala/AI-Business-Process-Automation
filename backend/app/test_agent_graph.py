from langgraph.types import Command

from app.services.agent_graph import agent_graph, checkpointer
from app.services.checkpointer import setup_checkpointer
from app.services.database_service import initialize_database


# The business action writes to the database, and PostgresSaver
# needs its checkpoint tables.
initialize_database()
setup_checkpointer(checkpointer)


print("\n==============================")
print("HUMAN APPROVAL TEST")
print("==============================")

question = (
    "For employee E102, explain the expense reimbursement process "
    "for a $750 expense, identify the employee's department and manager, "
    "and state what approval is required."
)

config = {
    "configurable": {
        "thread_id": "expense-E102-750"
    }
}


# ============================================================
# STEP 1: START AGENT
# ============================================================

print("\nQUESTION")
print("==============================")
print(question)

print("\nSTARTING AGENT...")
print("==============================")

result = agent_graph.invoke(
    {
        "question": question
    },
    config=config
)


# ============================================================
# STEP 2: HUMAN APPROVAL
# ============================================================

if "__interrupt__" in result:

    print("\nAGENT PAUSED")
    print("==============================")
    print(result)

    decision = input(
        "\nHUMAN APPROVAL\n"
        "==============================\n"
        "Enter approval decision (approve/reject): "
    ).strip().lower()

    if decision not in ["approve", "reject"]:
        print("\nInvalid decision. Use approve or reject.")
        raise SystemExit(1)

    # Resume the SAME graph execution
    result = agent_graph.invoke(
        Command(resume=decision),
        config=config
    )


# ============================================================
# STEP 3: FINAL RESULT
# ============================================================

print("\nFINAL RESULT")
print("==============================")

print(
    result.get(
        "answer",
        "No final answer generated."
    )
)


# ============================================================
# STEP 4: BUSINESS ACTION RESULT
# ============================================================

print("\nACTION RESULT")
print("==============================")

action_result = result.get(
    "action_result"
)

if action_result:
    print(action_result)
else:
    print("No business action was executed.")


# ============================================================
# STEP 5: STATUS
# ============================================================

print("\nWORKFLOW STATUS")
print("==============================")

print(
    "Approval Status:",
    result.get("approval_status", "not_required")
)

print(
    "Workflow Status:",
    result.get("status", "unknown")
)