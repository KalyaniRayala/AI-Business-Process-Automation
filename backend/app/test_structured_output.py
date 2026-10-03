from pprint import pprint

from app.services.rag_service import analyze_business_rule


question = "Who approves expenses above $500?"


result = analyze_business_rule(
    question=question
)


print("\n==============================")
print("STRUCTURED BUSINESS RULE")
print("==============================")

pprint(result)