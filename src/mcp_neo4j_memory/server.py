"""MCP Memory Server with Neo4j backend and semantic search.

This server provides persistent knowledge graph memory using Neo4j with
semantic search capabilities via embeddings.
"""

from __future__ import annotations
import os
from typing import List, Optional
from pydantic import BaseModel, Field
from neo4j import AsyncGraphDatabase, AsyncDriver
from loguru import logger
import numpy as np
from mcp.server.fastmcp import FastMCP
from model_providers import get_embedding_provider, EmbeddingProviderConfig
HAS_PROVIDERS = True


# Pydantic models matching MCP memory protocol
class Entity(BaseModel):
    """An entity in the knowledge graph."""
    name: str
    entityType: str = Field(alias="entityType")
    observations: List[str]


class Relation(BaseModel):
    """A relation between two entities."""
    from_entity: str = Field(alias="from")
    to_entity: str = Field(alias="to")
    relationType: str = Field(alias="relationType")


class Observation(BaseModel):
    """Observations to add to an entity."""
    entityName: str = Field(alias="entityName")
    contents: List[str]


class Deletion(BaseModel):
    """Observations to delete from an entity."""
    entityName: str = Field(alias="entityName")
    observations: List[str]


class Neo4jMemoryBackend:
    """Neo4j backend for knowledge graph storage with semantic search."""
    
    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
        embedding_dim: int = 1536
    ):
        self.uri = uri
        self.user = user
        self.password = password
        self.embedding_dim = embedding_dim
        self.driver: Optional[AsyncDriver] = None
        
        # Initialize embedding provider if available
        if HAS_PROVIDERS:
            try:
                emb_cfg = EmbeddingProviderConfig.from_env()
                resolved = get_embedding_provider(emb_cfg)
                self.embedding_provider = resolved.provider
                self.embedding_model = resolved.model_name
                logger.info(f"Initialized embedding provider: {self.embedding_model}")
            except Exception as e:
                logger.error(f"Failed to initialize embedding provider: {e}")
                self.embedding_provider = None
                self.embedding_model = None
        else:
            self.embedding_provider = None
            self.embedding_model = None
    
    async def connect(self):
        """Connect to Neo4j and create indexes."""
        self.driver = AsyncGraphDatabase.driver(
            self.uri,
            auth=(self.user, self.password)
        )
        
        # Verify connection
        await self.driver.verify_connectivity()
        logger.info(f"Connected to Neo4j at {self.uri}")
        
        # Create indexes for performance
        async with self.driver.session() as session:
            # Index on entity name
            await session.run(
                "CREATE INDEX entity_name_idx IF NOT EXISTS FOR (e:Entity) ON (e.name)"
            )
            # Index on entity type
            await session.run(
                "CREATE INDEX entity_type_idx IF NOT EXISTS FOR (e:Entity) ON (e.entityType)"
            )
            logger.info("Created Neo4j indexes")
    
    async def close(self):
        """Close Neo4j connection."""
        if self.driver:
            await self.driver.close()
    
    def _embed(self, text: str) -> Optional[List[float]]:
        """Generate embedding for text using the providers package.
        
        Note: The providers.embed() method is synchronous, not async.
        It takes a list of texts and returns a list of embeddings.
        """
        if not self.embedding_provider:
            return None
        
        try:
            # The provider.embed() expects a list of texts and returns list of embeddings
            embeddings = self.embedding_provider.embed([text])
            
            if embeddings and len(embeddings) > 0:
                return embeddings[0]
            else:
                logger.warning("No embeddings returned")
                return None
        except Exception as e:
            logger.error(f"Failed to generate embedding: {e}")
            return None
    
    async def create_entities(self, entities: List[Entity]) -> str:
        """Create entities in the knowledge graph."""
        async with self.driver.session() as session:
            created = 0
            
            for entity in entities:
                # Generate embedding for entity (name + observations)
                text = f"{entity.name} ({entity.entityType}): " + " ".join(entity.observations)
                embedding = self._embed(text)
                
                # Create entity node
                query = """
                MERGE (e:Entity {name: $name})
                ON CREATE SET 
                    e.entityType = $entityType,
                    e.observations = $observations,
                    e.embedding = $embedding,
                    e.created_at = timestamp(),
                    e.updated_at = timestamp()
                ON MATCH SET
                    e.updated_at = timestamp()
                RETURN e
                """
                
                result = await session.run(
                    query,
                    name=entity.name,
                    entityType=entity.entityType,
                    observations=entity.observations,
                    embedding=embedding
                )
                
                if await result.single():
                    created += 1
            
            logger.info(f"Created {created} entities in Neo4j")
            return f"Created {created} entities"
    
    async def create_relations(self, relations: List[Relation]) -> str:
        """Create relations between entities."""
        async with self.driver.session() as session:
            created = 0
            failed = []
            
            for relation in relations:
                # First check if both entities exist
                check_query = """
                MATCH (from:Entity {name: $from})
                MATCH (to:Entity {name: $to})
                RETURN from, to
                """
                
                check_result = await session.run(
                    check_query,
                    **{"from": relation.from_entity, "to": relation.to_entity}
                )
                
                if not await check_result.single():
                    failed.append(f"{relation.from_entity} -> {relation.to_entity} (one or both entities not found)")
                    logger.warning(f"Cannot create relation: entities '{relation.from_entity}' or '{relation.to_entity}' not found")
                    continue
                
                # Create the relation
                query = """
                MATCH (from:Entity {name: $from})
                MATCH (to:Entity {name: $to})
                MERGE (from)-[r:RELATES {type: $relationType}]->(to)
                ON CREATE SET r.created_at = timestamp()
                RETURN r
                """
                
                result = await session.run(
                    query,
                    **{"from": relation.from_entity, "to": relation.to_entity, "relationType": relation.relationType}
                )
                
                if await result.single():
                    created += 1
            
            message = f"Created {created} relations in Neo4j"
            if failed:
                message += f". Failed: {len(failed)} relations (entities not found: {', '.join(failed[:3])})"
                logger.warning(f"Failed to create {len(failed)} relations. Missing entities: {failed}")
            
            logger.info(message)
            return message
    
    async def add_observations(self, observations: List[Observation]) -> str:
        """Add observations to existing entities."""
        async with self.driver.session() as session:
            added = 0
            
            for obs in observations:
                # Get existing entity
                get_query = "MATCH (e:Entity {name: $name}) RETURN e"
                result = await session.run(get_query, name=obs.entityName)
                record = await result.single()
                
                if not record:
                    raise ValueError(f"Entity {obs.entityName} not found")
                
                entity_data = record["e"]
                old_observations = entity_data.get("observations", [])
                updated_observations = old_observations + obs.contents
                
                # Re-generate embedding with new observations
                entity_type = entity_data.get("entityType", "unknown")
                text = f"{obs.entityName} ({entity_type}): " + " ".join(updated_observations)
                embedding = self._embed(text)
                
                # Update entity
                update_query = """
                MATCH (e:Entity {name: $name})
                SET e.observations = $observations,
                    e.embedding = $embedding,
                    e.updated_at = timestamp()
                RETURN e
                """
                
                await session.run(
                    update_query,
                    name=obs.entityName,
                    observations=updated_observations,
                    embedding=embedding
                )
                
                added += len(obs.contents)
            
            logger.info(f"Added {added} observations")
            return f"Added {added} observations"
    
    async def delete_entities(self, entityNames: List[str]) -> str:
        """Delete entities and their relations."""
        async with self.driver.session() as session:
            query = """
            MATCH (e:Entity)
            WHERE e.name IN $names
            DETACH DELETE e
            """
            
            await session.run(query, names=entityNames)
            logger.info(f"Deleted entities: {entityNames}")
            return f"Deleted {len(entityNames)} entities"
    
    async def delete_observations(self, deletions: List[Deletion]) -> str:
        """Delete specific observations from entities."""
        async with self.driver.session() as session:
            deleted = 0
            
            for deletion in deletions:
                query = """
                MATCH (e:Entity {name: $name})
                SET e.observations = [obs IN e.observations WHERE NOT obs IN $toDelete]
                RETURN e
                """
                
                await session.run(
                    query,
                    name=deletion.entityName,
                    toDelete=deletion.observations
                )
                deleted += len(deletion.observations)
            
            logger.info(f"Deleted {deleted} observations")
            return f"Deleted {deleted} observations"
    
    async def delete_relations(self, relations: List[Relation]) -> str:
        """Delete specific relations."""
        async with self.driver.session() as session:
            for relation in relations:
                query = """
                MATCH (from:Entity {name: $from})-[r:RELATES {type: $relationType}]->(to:Entity {name: $to})
                DELETE r
                """
                
                await session.run(
                    query,
                    **{"from": relation.from_entity, "to": relation.to_entity, "relationType": relation.relationType}
                )
            
            return f"Deleted {len(relations)} relations"
    
    async def read_graph(self) -> dict:
        """Read the entire knowledge graph."""
        async with self.driver.session() as session:
            # Get all entities
            entities_query = """
            MATCH (e:Entity)
            RETURN e.name as name, e.entityType as entityType, e.observations as observations
            """
            entities_result = await session.run(entities_query)
            entities = []
            async for record in entities_result:
                entities.append({
                    "name": record["name"],
                    "entityType": record["entityType"],
                    "observations": record["observations"]
                })
            
            # Get all relations
            relations_query = """
            MATCH (from:Entity)-[r:RELATES]->(to:Entity)
            RETURN from.name as from, to.name as to, r.type as relationType
            """
            relations_result = await session.run(relations_query)
            relations = []
            async for record in relations_result:
                relations.append({
                    "from": record["from"],
                    "to": record["to"],
                    "relationType": record["relationType"]
                })
            
            return {
                "entities": entities,
                "relations": relations
            }
    
    async def search_nodes(self, query: str, k: int = 10) -> dict:
        """Semantic search using embeddings + graph traversal."""
        if not self.embedding_provider:
            # Fallback to keyword search
            return await self._keyword_search(query, k)
        
        # Generate query embedding
        query_embedding = self._embed(query)
        if not query_embedding:
            return await self._keyword_search(query, k)
        
        async with self.driver.session() as session:
            # Semantic search with graph context
            # Note: Neo4j doesn't have native cosine similarity in Cypher
            # We need to fetch candidates and compute similarity in Python
            
            # Get all entities with embeddings
            cypher_query = """
            MATCH (e:Entity)
            WHERE e.embedding IS NOT NULL
            RETURN e.name as name, 
                   e.entityType as entityType, 
                   e.observations as observations,
                   e.embedding as embedding
            LIMIT 100
            """
            
            result = await session.run(cypher_query)
            candidates = []
            async for record in result:
                candidates.append({
                    "name": record["name"],
                    "entityType": record["entityType"],
                    "observations": record["observations"],
                    "embedding": record["embedding"]
                })
            
            # Compute cosine similarity in Python
            scored_entities = []
            query_vec = np.array(query_embedding)
            
            for candidate in candidates:
                if candidate["embedding"]:
                    entity_vec = np.array(candidate["embedding"])
                    similarity = np.dot(query_vec, entity_vec) / (
                        np.linalg.norm(query_vec) * np.linalg.norm(entity_vec)
                    )
                    scored_entities.append({
                        **candidate,
                        "similarity": float(similarity)
                    })
            
            # Sort by similarity
            scored_entities.sort(key=lambda x: x["similarity"], reverse=True)
            top_entities = scored_entities[:k]
            
            # Get relations for top entities
            entity_names = [e["name"] for e in top_entities]
            relations_query = """
            MATCH (from:Entity)-[r:RELATES]->(to:Entity)
            WHERE from.name IN $names OR to.name IN $names
            RETURN from.name as from, to.name as to, r.type as relationType
            """
            
            relations_result = await session.run(relations_query, names=entity_names)
            relations = []
            async for record in relations_result:
                relations.append({
                    "from": record["from"],
                    "to": record["to"],
                    "relationType": record["relationType"]
                })
            
            # Remove embedding from output
            for entity in top_entities:
                entity.pop("embedding", None)
            
            return {
                "entities": top_entities,
                "relations": relations
            }
    
    async def _keyword_search(self, query: str, k: int = 10) -> dict:
        """Fallback keyword search when embeddings unavailable."""
        async with self.driver.session() as session:
            cypher_query = """
            MATCH (e:Entity)
            WHERE toLower(e.name) CONTAINS toLower($query)
               OR toLower(e.entityType) CONTAINS toLower($query)
               OR any(obs IN e.observations WHERE toLower(obs) CONTAINS toLower($query))
            RETURN e.name as name,
                   e.entityType as entityType,
                   e.observations as observations
            LIMIT $k
            """
            
            result = await session.run(cypher_query, query=query, k=k)
            entities = []
            async for record in result:
                entities.append({
                    "name": record["name"],
                    "entityType": record["entityType"],
                    "observations": record["observations"]
                })
            
            # Get relations
            entity_names = [e["name"] for e in entities]
            relations_query = """
            MATCH (from:Entity)-[r:RELATES]->(to:Entity)
            WHERE from.name IN $names OR to.name IN $names
            RETURN from.name as from, to.name as to, r.type as relationType
            """
            
            relations_result = await session.run(relations_query, names=entity_names)
            relations = []
            async for record in relations_result:
                relations.append({
                    "from": record["from"],
                    "to": record["to"],
                    "relationType": record["relationType"]
                })
            
            return {
                "entities": entities,
                "relations": relations
            }
    
    async def open_nodes(self, names: List[str]) -> dict:
        """Retrieve specific nodes by name."""
        async with self.driver.session() as session:
            # Get entities
            entities_query = """
            MATCH (e:Entity)
            WHERE e.name IN $names
            RETURN e.name as name, e.entityType as entityType, e.observations as observations
            """
            
            result = await session.run(entities_query, names=names)
            entities = []
            async for record in result:
                entities.append({
                    "name": record["name"],
                    "entityType": record["entityType"],
                    "observations": record["observations"]
                })
            
            # Get relations between these entities
            relations_query = """
            MATCH (from:Entity)-[r:RELATES]->(to:Entity)
            WHERE from.name IN $names AND to.name IN $names
            RETURN from.name as from, to.name as to, r.type as relationType
            """
            
            relations_result = await session.run(relations_query, names=names)
            relations = []
            async for record in relations_result:
                relations.append({
                    "from": record["from"],
                    "to": record["to"],
                    "relationType": record["relationType"]
                })
            
            return {
                "entities": entities,
                "relations": relations
            }


# Initialize FastMCP server
mcp = FastMCP("neo4j-memory", port=8000, host="0.0.0.0")

# Global backend instance
backend: Optional[Neo4jMemoryBackend] = None


async def get_backend() -> Neo4jMemoryBackend:
    """Get or create the Neo4j backend."""
    global backend
    if backend is None:
        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "password123")
        
        logger.info(f"Connecting to Neo4j at {uri} as user '{user}'")
        
        try:
            backend = Neo4jMemoryBackend(uri, user, password)
            await backend.connect()
            logger.info("Successfully connected to Neo4j")
        except Exception as e:
            logger.error(f"Failed to connect to Neo4j: {e}")
            logger.error(f"Check credentials - URI: {uri}, User: {user}")
            raise RuntimeError(f"Neo4j connection failed. Please check NEO4J_URI, NEO4J_USER, and NEO4J_PASSWORD environment variables. Error: {e}")
    
    return backend


# MCP Tools
@mcp.tool()
async def create_entities(entities: List[Entity]) -> str:
    """Create multiple new entities in the knowledge graph.
    
    Args:
        entities: List of entities to create. Each entity contains:
            - name: Entity identifier
            - entityType: Type classification
            - observations: List of facts about the entity
    
    Returns:
        Confirmation message with count of created entities
    """
    backend = await get_backend()
    return await backend.create_entities(entities)


@mcp.tool()
async def create_relations(relations: List[Relation]) -> str:
    """Create multiple new relations between entities.
    
    Args:
        relations: List of relations to create. Each relation contains:
            - from: Source entity name
            - to: Target entity name
            - relationType: Relationship type in active voice
    
    Returns:
        Confirmation message with count of created relations
    """
    backend = await get_backend()
    return await backend.create_relations(relations)


@mcp.tool()
async def add_observations(observations: List[Observation]) -> str:
    """Add new observations to existing entities.
    
    Args:
        observations: List of observations to add. Each observation contains:
            - entityName: Target entity name
            - contents: List of new observations to add
    
    Returns:
        Confirmation message with count of added observations
    """
    backend = await get_backend()
    return await backend.add_observations(observations)


@mcp.tool()
async def delete_entities(entityNames: List[str]) -> str:
    """Remove entities and their relations from the knowledge graph.
    
    Args:
        entityNames: List of entity names to delete
    
    Returns:
        Confirmation message
    """
    backend = await get_backend()
    return await backend.delete_entities(entityNames)


@mcp.tool()
async def delete_observations(deletions: List[Deletion]) -> str:
    """Remove specific observations from entities.
    
    Args:
        deletions: List of deletions. Each deletion contains:
            - entityName: Target entity name
            - observations: List of observations to remove
    
    Returns:
        Confirmation message
    """
    backend = await get_backend()
    return await backend.delete_observations(deletions)


@mcp.tool()
async def delete_relations(relations: List[Relation]) -> str:
    """Remove specific relations from the graph.
    
    Args:
        relations: List of relations to delete. Each relation contains:
            - from: Source entity name
            - to: Target entity name
            - relationType: Relationship type
    
    Returns:
        Confirmation message
    """
    backend = await get_backend()
    return await backend.delete_relations(relations)


@mcp.tool()
async def read_graph() -> dict:
    """Read the entire knowledge graph.
    
    Returns:
        Complete graph structure with all entities and relations
    """
    backend = await get_backend()
    return await backend.read_graph()


@mcp.tool()
async def search_nodes(query: str) -> dict:
    """Search for nodes based on semantic similarity and graph context.
    
    Uses embeddings for semantic search, then expands results with graph traversal.
    
    Args:
        query: Search query text
    
    Returns:
        Matching entities with their relations and similarity scores
    """
    backend = await get_backend()
    return await backend.search_nodes(query)


@mcp.tool()
async def open_nodes(names: List[str]) -> dict:
    """Retrieve specific nodes by name.
    
    Args:
        names: List of entity names to retrieve
    
    Returns:
        Requested entities and relations between them
    """
    backend = await get_backend()
    return await backend.open_nodes(names)


if __name__ == "__main__":
    # Run the MCP server
    mcp.run(transport="streamable-http")
