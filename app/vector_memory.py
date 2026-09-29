import json
import logging
from typing import List, Optional
import numpy as np
from app.db.session import get_db_connection

logger = logging.getLogger(__name__)

_embedding_model = None

def get_embedding_model():
    """Lazily load the local bge-small-en-v1.5 sentence transformer model."""
    global _embedding_model
    if _embedding_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading local embedding model BAAI/bge-small-en-v1.5...")
            _embedding_model = SentenceTransformer('BAAI/bge-small-en-v1.5')
            logger.info("Local embedding model loaded successfully.")
        except Exception as e:
            logger.error(f"Failed to load sentence-transformers model: {e}")
            _embedding_model = False
    return _embedding_model if _embedding_model is not False else None


def encode_text(text: str) -> Optional[List[float]]:
    """Encodes text into a 384-dimensional vector embedding."""
    if not text or not text.strip():
        return None
    model = get_embedding_model()
    if not model:
        return None
    try:
        vec = model.encode(text.strip(), convert_to_numpy=True)
        return vec.tolist()
    except Exception as e:
        logger.warning(f"Vector encoding failed: {e}")
        return None


def save_vector_memory(
    channel_id: str,
    user_id: str,
    content: str,
    role: str = "user"
) -> bool:
    """Encodes and saves a conversation memory turn into PostgreSQL user_memories table."""
    if role != "user" or not content or len(content.strip()) < 10:
        # Only index user messages of meaningful length
        return False

    embedding = encode_text(content)
    if not embedding:
        return False

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_memories (channel_id, user_id, content, role, embedding, created_at)
                VALUES (%s, %s, %s, %s, %s, now());
                """,
                (
                    channel_id or "default",
                    user_id or "unknown",
                    content.strip(),
                    role,
                    json.dumps(embedding),
                ),
            )
            conn.commit()
            logger.debug(f"Saved vector memory for channel '{channel_id}' / user '{user_id}'")
            return True
    except Exception as e:
        logger.warning(f"Failed to save vector memory to DB: {e}")
        return False
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def calculate_dynamic_top_k(query_text: str, default_top_k: int = 6) -> int:
    """
    Dynamically adjusts vector memory retrieval top_k based on user query intent.
    - Default: 6 chunks
    - Number detection (e.g. '10 messages', '8 points'): 1-20 chunks
    - Exhaustive intent (e.g. 'all opinions', 'full list of ideas'): 12 chunks
    """
    if not query_text:
        return default_top_k

    query_lower = query_text.lower()
    import re

    # 1. Check for explicit number requests like "10 messages", "8 points", "show 15 thoughts"
    num_match = re.search(r'\b(\d{1,2})\s*(messages|turns|chunks|opinions|points|comments|replies|ideas|thoughts|history)\b', query_lower)
    if num_match:
        try:
            val = int(num_match.group(1))
            if 1 <= val <= 20:
                logger.info(f"Dynamic top_k set to {val} based on explicit count request in query.")
                return val
        except ValueError:
            pass

    # 2. Check for exhaustive/comprehensive request keywords
    exhaustive_keywords = r'\b(all|every|full|entire|list of|summary of|everything)\b'
    topic_keywords = r'\b(opinions|messages|ideas|points|comments|thoughts|discussion|history)\b'
    if re.search(exhaustive_keywords, query_lower) and re.search(topic_keywords, query_lower):
        logger.info("Dynamic top_k scaled up to 12 based on exhaustive request in query.")
        return 12

    return default_top_k


def retrieve_relevant_memories(
    channel_id: str,
    query_text: str,
    top_k: Optional[int] = None,
    min_similarity: float = 0.35
) -> List[str]:
    """
    Retrieves top_k most semantically relevant memories for query_text
    from PostgreSQL using bge-small-en-v1.5 and cosine similarity.
    """
    if not query_text or not query_text.strip():
        return []

    if top_k is None:
        top_k = calculate_dynamic_top_k(query_text, default_top_k=6)

    query_vec = encode_text(query_text)
    if not query_vec:
        return []

    conn = None
    memories: List[str] = []
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Query candidate memories for this channel (or recent overall memories)
            cur.execute(
                """
                SELECT id, content, embedding 
                FROM user_memories 
                WHERE channel_id = %s OR channel_id = 'default'
                ORDER BY id DESC 
                LIMIT 100;
                """,
                (channel_id or "default",),
            )
            rows = cur.fetchall()

            if not rows:
                return []

            q_array = np.array(query_vec, dtype=np.float32)
            norm_q = np.linalg.norm(q_array)
            if norm_q == 0:
                return []

            scored_memories = []
            for r in rows:
                content = r["content"] if isinstance(r, dict) else r[1]
                emb_raw = r["embedding"] if isinstance(r, dict) else r[2]
                if not emb_raw:
                    continue
                try:
                    emb_list = json.loads(emb_raw) if isinstance(emb_raw, str) else emb_raw
                    c_array = np.array(emb_list, dtype=np.float32)
                    norm_c = np.linalg.norm(c_array)
                    if norm_c == 0:
                        continue
                    # Cosine similarity calculation
                    sim = float(np.dot(q_array, c_array) / (norm_q * norm_c))
                    if sim >= min_similarity:
                        scored_memories.append((sim, content))
                except Exception as ex:
                    logger.debug(f"Memory similarity calculation skipped for row: {ex}")
                    continue

            # Sort descending by similarity
            scored_memories.sort(key=lambda x: x[0], reverse=True)
            memories = [m[1] for m in scored_memories[:top_k]]

    except Exception as e:
        logger.warning(f"Vector memory retrieval failed cleanly: {e}")
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    return memories
