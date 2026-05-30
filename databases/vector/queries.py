import psycopg2
from psycopg2.extras import RealDictCursor

def get_db_connection():
    return psycopg2.connect(
        host="localhost",
        port=5400,
        database="transitflow",
        user="transitflow",
        password="transitflow"
    )

def query_policy_vector_search(query_embedding: list, limit: int = 5):
    """Search for similar policies using vector similarity."""
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    cur.execute("""
        SELECT title, content, 
               1 - (embedding <=> %s::vector) AS similarity
        FROM policy_documents
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """, (query_embedding, query_embedding, limit))
    
    results = cur.fetchall()
    cur.close()
    conn.close()
    return results
