from services.embedding_services import create_embeddings


texts = [
    "Expenses above $500 require manager approval.",
    "Finance checks the employee expense claim."
]


embeddings = create_embeddings(texts)


print("Number of embeddings:", len(embeddings))

print(
    "Dimensions of first embedding:",
    len(embeddings[0])
)

print(
    "First 10 values:",
    embeddings[0][:10]
)