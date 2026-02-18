# MCP Neo4j Memory Server

A Model Context Protocol (MCP) server that provides persistent knowledge graph memory using Neo4j with semantic search via embeddings.

## Features

- **Knowledge Graph Storage**: Neo4j for true graph database with Cypher queries
- **Semantic Search**: Uses your existing embedding provider for RAG capabilities
- **MCP Protocol**: Standard interface compatible with any MCP client
- **Entity & Relations**: Full support for knowledge graph with entities, observations, and relations
- **Hybrid Search**: Combines semantic similarity with graph traversal

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  MCP Neo4j Memory Server                             │
│  ┌───────────────────────────────────────────────┐  │
│  │  MCP Tools (FastMCP)                          │  │
│  │  - create_entities()                          │  │
│  │  - create_relations()                         │  │
│  │  - search_nodes() ← Semantic + Graph          │  │
│  │  - add_observations()                         │  │
│  │  - read_graph()                               │  │
│  └───────────────────────────────────────────────┘  │
│                       ↓                              │
│  ┌───────────────────────────────────────────────┐  │
│  │  Neo4j Backend                                │  │
│  │  - Entities as nodes                          │  │
│  │  - Relations as edges                         │  │
│  │  - Observations as properties                 │  │
│  │  - Embeddings stored on nodes                 │  │
│  │  - Cypher queries for graph traversal        │  │
│  └───────────────────────────────────────────────┘  │
│                       ↓                              │
│  ┌───────────────────────────────────────────────┐  │
│  │  Your Embedding Provider                      │  │
│  │  - from providers import get_embedding_...    │  │
│  │  - Azure OpenAI / Custom embeddings           │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

## Installation

```bash
cd server/mcp-neo4j-memory
uv sync
```

## Configuration

### 1. Neo4j Setup

**Docker (recommended):**
```bash
docker run -d \
  --name neo4j-memory \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password123 \
  -e NEO4J_PLUGINS='["apoc"]' \
  -v neo4j-data:/data \
  neo4j:latest
```

**Or use your existing Neo4j instance.**

### 2. Environment Variables

Create `.env` file:
```env
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password123

# Your embedding provider config (same as client)
AZURE_OPENAI_ENDPOINT=https://...
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-large
```

### 3. Add to mcp.json

```json
{
  "servers": {
    "neo4j-memory": {
      "command": "uv",
      "args": ["run", "python", "server/mcp-neo4j-memory/src/mcp_neo4j_memory/server.py"],
      "type": "stdio",
      "env": {
        "NEO4J_URI": "${NEO4J_URI}",
        "NEO4J_USER": "${NEO4J_USER}",
        "NEO4J_PASSWORD": "${NEO4J_PASSWORD}"
      }
    }
  }
}
```

## Usage

Once configured, your agent automatically gets these tools:

### Store Knowledge
```python
# Agent automatically uses this when you say:
# "Remember that I prefer Norwegian language"

create_entities([{
    "name": "user_preference_language",
    "entityType": "preference",
    "observations": ["Prefers Norwegian language"]
}])
```

### Create Relations
```python
# "I work at Anthropic"
create_relations([{
    "from": "user",
    "to": "Anthropic",
    "relationType": "works_at"
}])
```

### Semantic Search
```python
# "What do you know about my preferences?"
search_nodes("user preferences")
# Returns semantically similar entities with graph context
```

### Graph Traversal
```python
# "Tell me about my company and colleagues"
# Server automatically traverses: user -> works_at -> company -> employs -> colleagues
```

## Advanced Features

### Hybrid Search
Combines:
- **Semantic similarity** (via embeddings)
- **Graph proximity** (via Neo4j Cypher)
- **Recency weighting** (temporal decay)

### Multi-hop Reasoning
```cypher
MATCH (user:Entity {name: 'user'})-[r1*1..3]-(connected:Entity)
WHERE connected.embedding_similarity > 0.7
RETURN user, connected, relationships(r1)
```

### Temporal Memory
- Automatic timestamps on observations
- Decay old memories
- Boost recent interactions

## Cypher Examples

The server uses Neo4j's powerful Cypher query language:

```cypher
// Create entity with embedding
CREATE (e:Entity {
  name: 'John_Smith',
  entityType: 'person',
  observations: ['Speaks Norwegian'],
  embedding: [0.1, 0.2, ...],
  created_at: timestamp()
})

// Semantic + graph search
MATCH (e:Entity)
WHERE gds.similarity.cosine(e.embedding, $query_embedding) > 0.7
MATCH (e)-[r*0..2]-(related:Entity)
RETURN e, r, related
ORDER BY gds.similarity.cosine(e.embedding, $query_embedding) DESC
LIMIT 10
```

## Why Neo4j + Your Embeddings?

| Feature | JSONL | Vector DB Only | Neo4j + Embeddings |
|---------|-------|----------------|-------------------|
| Graph Relations | ❌ | ❌ | ✅ Native |
| Semantic Search | ❌ | ✅ | ✅ |
| Graph Traversal | ❌ | ❌ | ✅ Cypher |
| Multi-hop | ❌ | ❌ | ✅ |
| Visualization | ❌ | ❌ | ✅ Neo4j Browser |
| Production Ready | ❌ | ✅ | ✅ |
| Your Embeddings | N/A | Custom | ✅ Reuse existing |

## Testing

```bash
# Run tests
uv run pytest

# Test server standalone
uv run python src/mcp_neo4j_memory/server.py

# Query Neo4j browser
# Open http://localhost:7474
```

## Next Steps

1. Start Neo4j: `docker-compose up -d neo4j`
2. Add to `mcp.json`
3. Initialize client - server automatically connects
4. Chat with your agent - it will store memories in Neo4j

The agent will automatically:
- Store important facts as entities
- Create relations between concepts
- Search semantically when you ask questions
- Traverse the graph for context
