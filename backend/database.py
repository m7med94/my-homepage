# backend/database.py — SQLite WAL Persistence & Schema Initialization
import sqlite3
from contextlib import contextmanager
from backend.config import DB_PATH

@contextmanager
def get_db(timeout: float = 10.0):
    """Context manager for SQLite database connection."""
    conn = sqlite3.connect(DB_PATH, timeout=timeout)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    """Initializes SQLite schema in WAL mode with indexes and default seeds."""
    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS telemetry_logs (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                category TEXT NOT NULL,
                payload_data TEXT NOT NULL,
                client_ip TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_device_id ON telemetry_logs (device_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_category ON telemetry_logs (category);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON telemetry_logs (created_at);")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS todos (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                priority TEXT DEFAULT 'normal',
                completed INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_dispatch_logs (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                instruction TEXT NOT NULL,
                action TEXT NOT NULL,
                reply TEXT NOT NULL,
                plugin_name TEXT,
                latency_ms REAL DEFAULT 0.0,
                client_ip TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_device_id ON agent_dispatch_logs (device_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_action ON agent_dispatch_logs (action);")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS device_mqtt_credentials (
                device_id TEXT PRIMARY KEY,
                endpoint TEXT NOT NULL,
                client_id TEXT NOT NULL,
                username TEXT,
                password TEXT,
                publish_topic TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sora_memory (
                id TEXT PRIMARY KEY,
                category TEXT DEFAULT 'general',
                key TEXT NOT NULL,
                fact TEXT NOT NULL,
                importance INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sora_memory_key ON sora_memory (key);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sora_memory_cat ON sora_memory (category);")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS sora_chat_turns (
                id TEXT PRIMARY KEY,
                session_id TEXT DEFAULT 'default',
                role TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sora_turns_session ON sora_chat_turns (session_id, created_at);")

        # Seed default memories for Sora if empty
        cur.execute("SELECT COUNT(*) FROM sora_memory")
        if cur.fetchone()[0] == 0:
            default_memories = [
                ("mem_1", "user_profile", "user_name", "The user's name is Mohammed.", 5),
                ("mem_2", "hardware", "voice_assistant", "mo-project-c3 is the XiaoZhi voice assistant ESP32 for speech input/output.", 5),
                ("mem_3", "hardware", "notification_node", "esp32-2 is the sole dedicated notification receiver ESP32 for visual/LED alerts.", 5),
                ("mem_4", "preference", "response_style", "Mohammed prefers clear, natural, and concise responses in English.", 4),
            ]
            conn.executemany(
                "INSERT INTO sora_memory (id, category, key, fact, importance) VALUES (?, ?, ?, ?, ?)",
                default_memories,
            )
