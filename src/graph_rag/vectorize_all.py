import sqlite3
import json
import logging
import argparse
from pathlib import Path
from dotenv import load_dotenv
from src.utils.ai_gateway import AIGateway

# Model Config
MODEL_NAME = "BAAI/bge-m3"
BATCH_SIZE = 64

try:
    import chromadb
except ImportError:
    print("Vui lòng cài đặt thư viện cần thiết: pip install chromadb python-dotenv")
    exit(1)

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
    def __init__(self, use_local=False):
        self.use_local = use_local
        
        if use_local:
            try:
                import torch
                from sentence_transformers import SentenceTransformer
                device = "cuda" if torch.cuda.is_available() else "cpu"
                logger.info(f"Using local model {MODEL_NAME} on device: {device}")
                self.model = SentenceTransformer(MODEL_NAME, device=device)
            except ImportError:
                logger.error("Local mode requires 'sentence-transformers' and 'torch'. Fallback to Cloud.")
                self.use_local = False
                self.ai_gateway = AIGateway()
        else:
            logger.info("Initializing AI Gateway for NVIDIA remote embeddings...")
            self.ai_gateway = AIGateway()
        
        # Init ChromaDB
        self.chroma_client = chromadb.PersistentClient(path=str(VECTOR_STORE_PATH))
        
    def get_existing_ids(self, collection_name):
        """Lấy danh sách ID đã tồn tại để tránh vectorize lại"""
        try:
            collection = self.chroma_client.get_collection(name=collection_name)
            existing = collection.get(include=[])
            return set(existing['ids'])
        except:
            return set()

    def process_batch(self, collection, ids, documents, metadatas):
        """Xử lý và lưu trữ dữ liệu theo batch"""
        if not ids: return
        
        if self.use_local:
            logger.info(f"Embedding batch of {len(ids)} items locally...")
            embeddings = self.model.encode(documents, batch_size=BATCH_SIZE, show_progress_bar=True).tolist()
        else:
            logger.info(f"Embedding batch of {len(ids)} items via NVIDIA API...")
            embeddings = self.ai_gateway.embeddings(documents)
        
        collection.upsert(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
            documents=documents
        )

    def vectorize_communities(self):
        """Vectorize level 0 & 1 communities từ Graph DB"""
        logger.info("--- Vectorizing Communities from Graph ---")
        collection = self.chroma_client.get_or_create_collection(name="community_summaries")
        existing_ids = self.get_existing_ids("community_summaries")
        
        conn = sqlite3.connect(GRAPH_DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, level, title, summary, findings FROM communities")
        rows = cursor.fetchall()
        
        ids, docs, metas = [], [], []
        
        for cid, level, title, summary, findings in rows:
            v_id = f"comm_{cid}"
            if v_id in existing_ids: continue
            if not summary: continue
            
            text = f"Title: {title}\nLevel: {level}\nSummary: {summary}\nKey Findings: {findings}"
            
            # Lấy ship_ids từ Graph
            cursor.execute("SELECT id FROM nodes WHERE community_id = ? AND label = 'Ship'", (cid,))
            ship_ids = [row[0] for row in cursor.fetchall()]
            
            ids.append(v_id)
            docs.append(text)
            metas.append({
                "community_id": cid,
                "level": level,
                "title": title,
                "ship_ids": json.dumps(ship_ids)
            })
            
        self.process_batch(collection, ids, docs, metas)
        conn.close()

    def vectorize_skills(self):
        """Vectorize skill descriptions từ SQL DB"""
        logger.info("--- Vectorizing Skills from SQL ---")
        collection = self.chroma_client.get_or_create_collection(name="entity_mechanics")
        existing_ids = self.get_existing_ids("entity_mechanics")
        
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
        for sk_id, sk_name, sk_desc, sh_name, sh_id in cursor.fetchall():
            v_id = f"skill_{sk_id}"
            if v_id in existing_ids: continue
            if not sk_desc: continue
            
            text = f"Skill: {sk_name} (Ship: {sh_name})\nDescription: {sk_desc}"
            ids.append(v_id)
            docs.append(text)
            metas.append({
                "entity_id": sk_id,
                "type": "skill",
                "parent_ship_name": sh_name,
                "parent_ship_id": sh_id
            })
            
        self.process_batch(collection, ids, docs, metas)
        conn.close()

    def vectorize_ships_basic(self):
        """Vectorize thông tin cơ bản của tàu cho Entity Search"""
        logger.info("--- Vectorizing Ship Entities from SQL ---")
        collection = self.chroma_client.get_or_create_collection(name="entity_mechanics")
        existing_ids = self.get_existing_ids("entity_mechanics")
        
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
        for sid, name, nation, hull, rarity, tags in cursor.fetchall():
            v_id = f"ship_{sid}"
            if v_id in existing_ids: continue
            
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
            
            ids.append(v_id)
            docs.append(text)
            metas.append({
                "entity_id": sid,
                "type": "ship",
                "name": name,
                "hull": hull,
                "nation": nation
            })
            
        self.process_batch(collection, ids, docs, metas)
        conn.close()

    def vectorize_voice_lines(self, limit=1000):
        """Vectorize lời thoại từ SQL DB"""
        logger.info(f"--- Vectorizing Voice Lines (Limit: {limit}) ---")
        collection = self.chroma_client.get_or_create_collection(name="character_lore")
        existing_ids = self.get_existing_ids("character_lore")
        
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
            if vector_id in existing_ids: continue
            
            text = f"{s_name} ({v_type}): {content}"
            ids.append(vector_id)
            docs.append(text)
            metas.append({
                "voice_id": v_id,
                "ship_id": s_id,
                "ship_name": s_name,
                "type": v_type
            })
            
            if len(ids) >= 1000: # Batch size lớn hơn cho Voice Lines
                self.process_batch(collection, ids, docs, metas)
                ids, docs, metas = [], [], []
                
        self.process_batch(collection, ids, docs, metas)
        conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Azur Lane Vectorizer")
    parser.add_argument("--local", action="store_true", help="Use local SentenceTransformer instead of NVIDIA API")
    args = parser.parse_args()

    vectorizer = AzurLaneVectorizer(use_local=args.local)
    
    # 1. Communities (Graph)
    vectorizer.vectorize_communities()
    
    # 2. Entity Mechanics (Skills & Basic Ship Info)
    vectorizer.vectorize_skills()
    vectorizer.vectorize_ships_basic()
    
    # 3. Character Lore (Voice Lines)
    # Tăng limit lên nếu muốn chạy nhiều hơn, hoặc set None để chạy Full
    vectorizer.vectorize_voice_lines(limit=50000) 
    
    logger.info("Vectorization Process Completed Successfully.")
