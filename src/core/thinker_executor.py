import sqlite3
import json
import logging
import chromadb
from pathlib import Path
from sentence_transformers import SentenceTransformer
from typing import Dict, List, Any

# Configuration
BASE_DIR = Path(__file__).resolve().parents[2]
SQL_DB_PATH = BASE_DIR / "data" / "azur_lane.db"
GRAPH_DB_PATH = BASE_DIR / "data" / "azur_lane_graph.db"
VECTOR_STORE_PATH = BASE_DIR / "data" / "chroma_db"
MODEL_NAME = "BAAI/bge-m3"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

class ThinkerExecutor:
    def __init__(self):
        self.chroma_client = chromadb.PersistentClient(path=str(VECTOR_STORE_PATH))
        self.embed_model = SentenceTransformer(MODEL_NAME)
        
    def _get_db_conn(self, path: Path):
        return sqlite3.connect(path)

    def execute_sql(self, query: str) -> List[Dict]:
        """Executes a SQL query against the relational database."""
        try:
            with self._get_db_conn(SQL_DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(query)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"SQL Execution Error: {e}")
            return [{"error": str(e)}]

    def execute_graph(self, query: str) -> List[Dict]:
        """Executes a SQL query against the graph (nodes/edges) database."""
        try:
            with self._get_db_conn(GRAPH_DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(query)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Graph Execution Error: {e}")
            return [{"error": str(e)}]

    def execute_vector(self, query_text: str, n_results: int = 5) -> List[Dict]:
        """Performs semantic search across all relevant collections."""
        results = []
        collections = ["community_summaries", "entity_mechanics", "character_lore"]
        
        query_embedding = self.embed_model.encode(query_text).tolist()
        
        for coll_name in collections:
            try:
                coll = self.chroma_client.get_collection(name=coll_name)
                res = coll.query(
                    query_embeddings=[query_embedding],
                    n_results=n_results,
                    include=["documents", "metadatas", "distances"]
                )
                
                for i in range(len(res["ids"][0])):
                    results.append({
                        "collection": coll_name,
                        "content": res["documents"][0][i],
                        "metadata": res["metadatas"][0][i],
                        "distance": res["distances"][0][i]
                    })
            except Exception as e:
                logger.warning(f"Vector search failed for {coll_name}: {e}")
                
        # Sort by distance (relevance)
        return sorted(results, key=lambda x: x["distance"])[:n_results]

    def process_plan(self, plan_json: Dict) -> Dict[str, Any]:
        """Iterates through the dispatcher's plan and gathers all evidence."""
        logger.info(f"Processing Plan: {plan_json.get('intent')} (Complexity: {plan_json.get('complexity')})")
        
        context = {
            "intent": plan_json.get("intent"),
            "complexity": plan_json.get("complexity"),
            "reasoning": plan_json.get("reasoning"),
            "findings": []
        }

        execution_plan = plan_json.get("execution_plan", {})
        steps = execution_plan.get("steps", [])

        for step in steps:
            action = step.get("action")
            query = step.get("query") or step.get("target")
            
            logger.info(f"Executing Step: {action} -> {query}")
            
            if action in ["query_sql", "sql"]:
                res = self.execute_sql(query)
                context["findings"].append({"step": action, "data": res})
            elif action in ["search_vector", "vector"]:
                res = self.execute_vector(query)
                context["findings"].append({"step": action, "data": res})
            elif action in ["traverse_graph", "graph"]:
                res = self.execute_graph(query)
                context["findings"].append({"step": action, "data": res})
            else:
                logger.warning(f"Unknown action: {action}")

        return context

if __name__ == "__main__":
    # Example usage with a mock dispatcher plan
    mock_plan = {
        "intent": "meta_comparison",
        "complexity": "medium",
        "execution_plan": {
            "steps": [
                { 
                    "action": "query_sql", 
                    "query": """
                        SELECT s.name, r.name as rarity 
                        FROM ships s 
                        JOIN rarities r ON s.rarity_id = r.id 
                        WHERE s.name IN ('Enterprise', 'Taihou')
                    """ 
                },
                { "action": "search_vector", "query": "anti-air skills of Enterprise and Taihou" }
            ]
        }
    }
    
    executor = ThinkerExecutor()
    final_context = executor.process_plan(mock_plan)
    print(json.dumps(final_context, indent=2))
