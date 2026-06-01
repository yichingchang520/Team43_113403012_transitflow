## Section 4 — Vector / RAG Design

### What is embedded and why cosine similarity

The policy documents stored in the `policy_documents` table are converted
into vector embeddings — numerical representations that capture the semantic
meaning of each document. We use the `nomic-embed-text` model (Ollama) which
produces 768-dimensional vectors.

Cosine similarity is used as the distance metric because it is
magnitude-independent: it measures the angle between two vectors in the
embedding space rather than their absolute distance. This means a short
question like "can I bring my bike?" and a longer policy paragraph about
bicycles will still have high similarity if they describe the same concept,
regardless of their different lengths. This makes it appropriate for
matching short user queries against longer policy documents.

### The Full RAG Pipeline

The system uses Retrieval-Augmented Generation (RAG) in four stages:

1. Query Embedding
   When a user asks a policy question (e.g. "can I get a refund?"),
   the question text is converted into a 768-dimensional vector using
   the same nomic-embed-text model used during seeding.

2. Similarity Search
   The query vector is compared against all stored policy document
   embeddings using cosine similarity via pgvector's <=> operator.
   The top N most similar documents are retrieved:
   SELECT title, content, 1 - (embedding <=> query_vector) AS similarity
   FROM policy_documents ORDER BY similarity DESC LIMIT 5

3. Retrieved Documents
   The top matching policy documents (title + content) are passed
   to the LLM as context, alongside the original user question.

4. LLM Answer Generation
   The LLM reads the retrieved documents and the user question,
   then generates a natural language answer grounded in the
   policy content — not from its own training data.

### Embedding Dimension and Provider Switch

Our implementation uses vector(768) which matches the Ollama
nomic-embed-text embedding model. If the team switches to Gemini
(gemini-embedding-001), which produces 3072-dimensional vectors,
the stored embeddings become incompatible with new query embeddings.
This causes a dimension mismatch error that makes the HNSW index
completely unusable. The entire policy_documents table must be
wiped and re-seeded after changing providers. This means all team
members must agree on one provider before seeding.


## Section 5 — AI Tool Usage Evidence

### Example 1 — Generating policy document content
**Context:** I needed to add some new detailed policy text for the pgvector
database covering lost property, accessibility, engineering works, penalty fares, delay compensation policy and renewing some details on booking rules.

**Prompt:** "Write a detailed refund policy for a transit company covering
both Metro and National Rail. Use USD currency. Reference real station
names like Central Square (MS01) and Central Station (NR01)."

**Outcome:** AI generated detailed policy text. I reviewed it and adjusted
the fare figures to match the exact values in our JSON files ($2.50 base
fare, $1.50 per stop for National Rail standard class).

---

### Example 2 — Understanding pgvector and cosine similarity
**Context:** I needed to explain cosine similarity for Section 4 of
the design document but didn't fully understand why it was used.

**Prompt:** "Explain why cosine similarity is used for semantic search
in pgvector, not just 'it measures similarity' but the actual reason."

**Outcome:** AI explained that cosine similarity is magnitude-independent
and measures directional similarity — this was the specific language
the marking guide required. My initial draft just said "it measures
how similar things are" which would have lost marks.

---

### Example 3 — AI gave wrong output that needed correction
**Context:** I asked AI to write queries.py using its own connection
pattern instead of the project's _connect() helper.

**Prompt:** "Write query_policy_vector_search() for pgvector search."

**Outcome:** AI generated get_db_connection() instead of using _connect().
I identified this by re-reading AI_SESSION_CONTEXT.md which clearly
states the _connect() pattern must be used. I corrected the function
to use the project's required pattern.


## Section 6 — Reflection & Trade-offs

### Trade-off: Embedding provider lock-in
We chose vector(768) for Ollama nomic-embed-text because it runs
locally without an API key, making development easier for all team
members. However, this means we are locked into 768 dimensions —
switching to Gemini (3072 dimensions) after seeding requires wiping
and re-seeding the entire policy_documents table. In a production
system we would abstract the embedding dimension into a configuration
variable and include a migration script to handle provider switches
without data loss.
