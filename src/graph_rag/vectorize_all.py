import sqlite3
import json
import hashlib
import logging
import argparse
from pathlib import Path
from dotenv import load_dotenv

# Model Config
MODEL_NAME = "BAAI/bge-m3"
BATCH_SIZE = 64

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Paths
BASE_DIR = Path(__file__).resolve().parents[2]
SQL_DB_PATH = BASE_DIR / "data" / "azur_lane.db"
GRAPH_DB_PATH = BASE_DIR / "data" / "azur_lane_graph.db"
VECTOR_STORE_PATH = BASE_DIR / "data" / "chroma_db"

class AzurLaneVectorizer:
    def __init__(self, use_local=False, force=False):
        self.use_local = use_local
        self.force = force
        self.model = None
        self.ai_gateway = None
        try:
            import chromadb
        except ImportError as error:
            raise ImportError("Missing dependency 'chromadb'. Install requirements before vectorization.") from error

        # Init ChromaDB
        self.chroma_client = chromadb.PersistentClient(path=str(VECTOR_STORE_PATH))
        
    def get_existing_ids(self, collection_name):
        """Lấy danh sách ID đã tồn tại để tránh vectorize lại"""
        if self.force:
            return set()
        try:
            collection = self.chroma_client.get_collection(name=collection_name)
            existing = collection.get(include=[])
            return set(existing['ids'])
        except:
            return set()

    def get_existing_hashes(self, collection_name):
        """Return {vector_id: content_hash} for skip/update decisions."""
        if self.force:
            return {}
        try:
            collection = self.chroma_client.get_collection(name=collection_name)
            existing = collection.get(include=["metadatas"])
            return {
                item_id: (metadata or {}).get("content_hash", "")
                for item_id, metadata in zip(existing["ids"], existing["metadatas"])
            }
        except:
            return {}

    @staticmethod
    def _compute_hash(text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def process_batch(self, collection, ids, documents, metadatas):
        """Xử lý và lưu trữ dữ liệu theo batch"""
        if not ids:
            return

        self._ensure_embedding_backend()
        
        all_embeddings = []
        if self.use_local:
            logger.info(f"Embedding batch of {len(ids)} items locally...")
            all_embeddings = self.model.encode(documents, batch_size=BATCH_SIZE, show_progress_bar=True).tolist()
        else:
            logger.info(f"Embedding {len(ids)} items via Cloudflare Worker (Batch size: {BATCH_SIZE})...")
            for i in range(0, len(ids), BATCH_SIZE):
                batch_docs = documents[i : i + BATCH_SIZE]
                batch_embeddings = self.ai_gateway.embeddings(batch_docs)
                all_embeddings.extend(batch_embeddings)
        
        collection.upsert(
            ids=ids,
            embeddings=all_embeddings,
            metadatas=metadatas,
            documents=documents
        )

    def _ensure_embedding_backend(self):
        if self.use_local:
            if self.model is not None:
                return
            try:
                import torch
                from sentence_transformers import SentenceTransformer

                device = "cuda" if torch.cuda.is_available() else "cpu"
                logger.info(f"Using local model {MODEL_NAME} on device: {device}")
                self.model = SentenceTransformer(MODEL_NAME, device=device)
                return
            except ImportError:
                logger.error("Local mode requires 'sentence-transformers' and 'torch'. Fallback to Cloud.")
                self.use_local = False

        if self.ai_gateway is None:
            from src.utils.ai_gateway import AIGateway
            logger.info("Initializing AI Gateway for Cloudflare remote embeddings...")
            self.ai_gateway = AIGateway()

    def vectorize_communities(self):
        """Vectorize level 0 & 1 communities từ Graph DB"""
        logger.info("--- Vectorizing Communities from Graph ---")
        collection = self.chroma_client.get_or_create_collection(name="community_summaries")
        existing_hashes = self.get_existing_hashes("community_summaries")
        
        conn = sqlite3.connect(GRAPH_DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, level, title, summary, findings FROM communities")
        rows = cursor.fetchall()
        
        ids, docs, metas = [], [], []
        new_count, updated_count, skipped_count = 0, 0, 0
        
        for cid, level, title, summary, findings in rows:
            v_id = f"comm_{cid}"
            if not summary: continue
            
            text = f"Title: {title}\nLevel: {level}\nSummary: {summary}\nKey Findings: {findings}"
            content_hash = self._compute_hash(text)
            if existing_hashes.get(v_id) == content_hash:
                skipped_count += 1
                continue
            if v_id in existing_hashes:
                updated_count += 1
            else:
                new_count += 1
            
            # Lấy ship_ids từ Graph
            cursor.execute("SELECT id FROM nodes WHERE community_id = ? AND label = 'Ship'", (cid,))
            ship_ids = [row[0] for row in cursor.fetchall()]
            
            ids.append(v_id)
            docs.append(text)
            metas.append({
                "community_id": cid,
                "level": level,
                "title": title,
                "ship_ids": json.dumps(ship_ids),
                "content_hash": content_hash
            })
            
        self.process_batch(collection, ids, docs, metas)
        conn.close()
        logger.info(f"Communities: {new_count} new, {updated_count} updated, {skipped_count} unchanged")

    def vectorize_skills(self):
        """Vectorize skill descriptions từ SQL DB"""
        logger.info("--- Vectorizing Skills from SQL ---")
        collection = self.chroma_client.get_or_create_collection(name="entity_mechanics")
        existing_hashes = self.get_existing_hashes("entity_mechanics")
        
        conn = sqlite3.connect(SQL_DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT s.id, s.name, s.description, sh.name as ship_name, sh.id as ship_id
            FROM skills s
            JOIN ship_skills ss ON ss.skill_id = s.id
            JOIN ships sh ON sh.id = ss.ship_id
            GROUP BY s.id
        """)
        
        ids, docs, metas = [], [], []
        new_count, updated_count, skipped_count = 0, 0, 0
        for sk_id, sk_name, sk_desc, sh_name, sh_id in cursor.fetchall():
            v_id = f"skill_{sk_id}"
            if not sk_desc: continue
            
            text = f"Skill: {sk_name} (Ship: {sh_name})\nDescription: {sk_desc}"
            content_hash = self._compute_hash(text)
            if existing_hashes.get(v_id) == content_hash:
                skipped_count += 1
                continue
            if v_id in existing_hashes:
                updated_count += 1
            else:
                new_count += 1

            ids.append(v_id)
            docs.append(text)
            metas.append({
                "entity_id": sk_id,
                "type": "skill",
                "parent_ship_name": sh_name,
                "parent_ship_id": sh_id,
                "content_hash": content_hash
            })
            
        self.process_batch(collection, ids, docs, metas)
        conn.close()
        logger.info(f"Skills: {new_count} new, {updated_count} updated, {skipped_count} unchanged")

    def vectorize_ships_basic(self):
        """Vectorize thông tin cơ bản của tàu cho Entity Search"""
        logger.info("--- Vectorizing Ship Entities from SQL ---")
        collection = self.chroma_client.get_or_create_collection(name="entity_mechanics")
        existing_hashes = self.get_existing_hashes("entity_mechanics")
        
        conn = sqlite3.connect(SQL_DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT s.id, s.name, n.name as nation, h.name as hull, r.name as rarity, s.tags
            FROM ships s
            JOIN nations n ON s.nation_id = n.id
            JOIN hulls h ON s.hull_id = h.id
            JOIN rarities r ON s.rarity_id = r.id
        """)
        
        ids, docs, metas = [], [], []
        new_count, updated_count, skipped_count = 0, 0, 0
        for sid, name, nation, hull, rarity, tags in cursor.fetchall():
            v_id = f"ship_{sid}"
            
            raw_tags = json.loads(tags) if tags else []
            tags_list = []
            for item in raw_tags:
                if isinstance(item, list):
                    tags_list.extend(item)
                else:
                    tags_list.append(item)
            # Remove duplicates and ensure tags are strings
            tags_list = sorted(list(set(str(t) for t in tags_list if t)))
            
            text = f"Ship: {name}\nFaction: {nation}\nHull: {hull}\nRarity: {rarity}\nTags: {', '.join(tags_list)}"
            content_hash = self._compute_hash(text)
            if existing_hashes.get(v_id) == content_hash:
                skipped_count += 1
                continue
            if v_id in existing_hashes:
                updated_count += 1
            else:
                new_count += 1
            
            ids.append(v_id)
            docs.append(text)
            metas.append({
                "entity_id": sid,
                "type": "ship",
                "name": name,
                "hull": hull,
                "nation": nation,
                "content_hash": content_hash
            })
            
        self.process_batch(collection, ids, docs, metas)
        conn.close()
        logger.info(f"Ships: {new_count} new, {updated_count} updated, {skipped_count} unchanged")

    def vectorize_voice_lines(self, limit=1000):
        """Vectorize lời thoại từ SQL DB"""
        logger.info(f"--- Vectorizing Voice Lines (Limit: {limit}) ---")
        collection = self.chroma_client.get_or_create_collection(name="character_lore")
        existing_hashes = self.get_existing_hashes("character_lore")
        
        conn = sqlite3.connect(SQL_DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT v.id, v.content, v.type, s.name as ship_name, s.id as ship_id
            FROM voice_lines v
            JOIN ships s ON v.ship_id = s.id
            WHERE v.content IS NOT NULL AND v.content != ''
            LIMIT ?
        """, (limit,))
        
        ids, docs, metas = [], [], []
        for v_id, content, v_type, s_name, s_id in cursor.fetchall():
            vector_id = f"voice_{v_id}"
            
            text = f"{s_name} ({v_type}): {content}"
            content_hash = self._compute_hash(text)
            if existing_hashes.get(vector_id) == content_hash:
                continue

            ids.append(vector_id)
            docs.append(text)
            metas.append({
                "voice_id": v_id,
                "ship_id": s_id,
                "ship_name": s_name,
                "type": v_type,
                "content_hash": content_hash
            })
            
            if len(ids) >= 1000: # Batch size lớn hơn cho Voice Lines
                self.process_batch(collection, ids, docs, metas)
                ids, docs, metas = [], [], []
                
        self.process_batch(collection, ids, docs, metas)
        conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Azur Lane Vectorizer")
    parser.add_argument("--local", action="store_true", help="Use local SentenceTransformer instead of Cloudflare Worker")
    parser.add_argument("--force", action="store_true", help="Force update existing entries in ChromaDB")
    args = parser.parse_args()

    vectorizer = AzurLaneVectorizer(use_local=args.local, force=args.force)
    
    # 1. Communities (Graph)
    vectorizer.vectorize_communities()
    
    # 2. Entity Mechanics (Skills & Basic Ship Info)
    vectorizer.vectorize_skills()
    vectorizer.vectorize_ships_basic()
    
    # 3. Character Lore (Voice Lines) - DISABLED for faster re-indexing of skills
    # vectorizer.vectorize_voice_lines(limit=50000) 
    
    logger.info("Vectorization Process Completed Successfully.")
