from app.services.rag_service import answer_question


question = "Who approves expenses above $500?"


answer = answer_question(question)


print("\n==============================")
print("RAG ANSWER")
print("==============================")

print(answer)