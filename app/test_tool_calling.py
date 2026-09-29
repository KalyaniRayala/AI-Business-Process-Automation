from app.services.tool_calling_service import run_tool_agent


question ="Does an expense of $400 require approval, and also  $6000 require approval?"


answer = run_tool_agent(question)


print("\n==============================")
print("TOOL VALIDATION TEST")
print("==============================")

print("\nQUESTION")
print("==============================")
print(question)

print("\nANSWER")
print("==============================")
print(answer)