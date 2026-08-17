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
            CREATE TABLE IF NOT EXISTS playlists (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS playlist_tracks (
                id TEXT PRIMARY KEY,
                playlist_id TEXT NOT NULL,
                track_filename TEXT NOT NULL,
                track_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_playlist_id ON playlist_tracks (playlist_id);")

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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_created_at ON agent_dispatch_logs (created_at);")

        # Seed sample todos if table is empty
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM todos")
        if cur.fetchone()[0] == 0:
            sample_todos = [
                ("1", "Check XiaoZhi ESP32 battery level & charging dock", "high", 0),
                ("2", "Calibrate living room DHT22 temperature sensor", "normal", 0),
                ("3", "Verify automated SQLite WAL backup schedule", "routine", 1),
            ]
            conn.executemany(
                "INSERT INTO todos (id, text, priority, completed) VALUES (?, ?, ?, ?)",
                sample_todos,
            )

        # Seed default favorites playlist if empty
        cur.execute("SELECT COUNT(*) FROM playlists")
        if cur.fetchone()[0] == 0:
            conn.execute(
                "INSERT INTO playlists (id, name, description) VALUES (?, ?, ?)",
                ("favs", "Favorites", "Top rotation tracks & ambient audio")
            )
