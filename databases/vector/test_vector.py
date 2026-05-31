from skeleton.llm_provider import llm
from databases.vector import query_policy_vector_search

# Test with a real question
query = "delay compensation 45 minutes"
print(f"Testing: {query}")
print("-" * 40)

# Get embedding for the question
embedding = llm.embed(query)

# Search the vector database - use 'limit' not 'match_count'
results = query_policy_vector_search(embedding, limit=3)

print(f"Found {len(results)} results:\n")
for i, r in enumerate(results, 1):
    print(f"{i}. Title: {r['title']}")
    print(f"   Similarity: {r['similarity']:.3f}")
    print(f"   Content preview: {r['content'][:100]}...")
    print()
