# backend/routers/music.py — Music Storage, Playlists & Voice Resolver Routes
import json
import os
import secrets
import shutil
import sqlite3
import urllib.parse
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from fastapi import APIRouter, File, Form, HTTPException, Request, Query, UploadFile, status
from pydantic import BaseModel, Field

from backend.config import (
    DB_PATH,
    MUSIC_DIR,
    ALLOWED_AUDIO_EXTENSIONS,
    require_dashboard_session,
)
from backend.events import subscribers
from backend.audio import convert_to_esp32_opus

router = APIRouter(tags=["Music & Playlists"])

class PlaylistCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120, description="Playlist title")
    description: Optional[str] = Field("", max_length=500)

class PlaylistAddTrack(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)

def voice_music_action(query: str = "", action: str = "play") -> Dict[str, Any]:
    """Helper used both by HTTP endpoint and by Universal Agent Hub."""
    available_tracks = []
    if os.path.exists(MUSIC_DIR):
        for f in sorted(os.listdir(MUSIC_DIR)):
            if os.path.splitext(f)[1].lower() in ALLOWED_AUDIO_EXTENSIONS:
                available_tracks.append({
                    "filename": f,
                    "title": os.path.splitext(f)[0],
                    "url": f"/music/{urllib.parse.quote(f)}"
                })

    if not available_tracks:
        return {
            "status": "warning",
            "found": False,
            "tts_message": "Your music vault is currently empty. Please upload some songs to your server first.",
            "summary": "Your music vault is currently empty. Please upload some songs to your server first.",
            "voice_summary": "Your music vault is currently empty. Please upload some songs to your server first.",
        }

    q = (query or "").strip().lower()

    if action == "list" or "what" in q or "list" in q or "songs" in q:
        titles = [t["title"] for t in available_tracks[:5]]
        count = len(available_tracks)
        summary = f"You have {count} track{'s' if count > 1 else ''} in your library, including: {', '.join(titles)}."
        return {
            "status": "success",
            "found": True,
            "tts_message": summary,
            "summary": summary,
            "voice_summary": summary,
            "tracks": available_tracks
        }

    def get_track_payload(t, act="play", prefix=""):
        base = os.path.splitext(t["filename"])[0]
        ogg_candidate = f"{base}.ogg"
        if os.path.exists(os.path.join(MUSIC_DIR, ogg_candidate)):
            esp32_url = f"/music/{urllib.parse.quote(ogg_candidate)}"
        else:
            esp32_url = t["url"]
        
        msg = f"{prefix}Playing {t['title']}." if prefix else f"Playing {t['title']}."
        return {
            "status": "success",
            "found": True,
            "action": act,
            "tts_message": msg,
            "summary": msg,
            "voice_summary": msg,
            "track": t,
            "url": t["url"],
            "esp32_url": esp32_url,
            "filename": t["filename"]
        }

    if not q or "random" in q or "anything" in q or "shuffle" in q or q == "music":
        selected = available_tracks[secrets.randbelow(len(available_tracks))]
        return get_track_payload(selected, "play")

    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        p_match = conn.execute("SELECT id, name FROM playlists WHERE lower(name) LIKE ?", (f"%{q}%",)).fetchone()
        if p_match:
            t_rows = conn.execute("SELECT track_filename FROM playlist_tracks WHERE playlist_id = ? ORDER BY track_order ASC", (p_match["id"],)).fetchall()
            valid_p_tracks = [t["track_filename"] for t in t_rows if os.path.exists(os.path.join(MUSIC_DIR, t["track_filename"]))]
            if valid_p_tracks:
                first_song = valid_p_tracks[0]
                target_t = {"filename": first_song, "title": os.path.splitext(first_song)[0], "url": f"/music/{urllib.parse.quote(first_song)}"}
                return get_track_payload(target_t, "play_playlist", f"Playing {p_match['name']} playlist starting with ")

    matched = None
    for t in available_tracks:
        if q in t["title"].lower() or t["title"].lower() in q:
            matched = t
            break

    if not matched:
        matched = available_tracks[0]
        return get_track_payload(matched, "play", f"Could not find an exact match for {query}. ")

    return get_track_payload(matched, "play")

@router.get("/api/v1/music", summary="List Uploaded Music & Audio Tracks")
def list_music_files(request: Request):
    """Lists all audio tracks available on the server."""
    require_dashboard_session(request)
    tracks = []
    if os.path.exists(MUSIC_DIR):
        for fname in sorted(os.listdir(MUSIC_DIR)):
            ext = os.path.splitext(fname)[1].lower()
            if ext in ALLOWED_AUDIO_EXTENSIONS:
                fpath = os.path.join(MUSIC_DIR, fname)
                try:
                    fstat = os.stat(fpath)
                    tracks.append({
                        "filename": fname,
                        "title": os.path.splitext(fname)[0],
                        "extension": ext.replace(".", "").upper(),
                        "size_bytes": fstat.st_size,
                        "size_mb": round(fstat.st_size / (1024 * 1024), 2),
                        "url": f"/music/{urllib.parse.quote(fname)}",
                        "created_at": datetime.fromtimestamp(fstat.st_mtime, timezone.utc).isoformat(),
                    })
                except Exception:
                    pass

    return {
        "status": "success",
        "count": len(tracks),
        "tracks": tracks,
    }

@router.post("/api/v1/music/upload", status_code=status.HTTP_201_CREATED, summary="Upload Music File (Single Part)")
async def upload_music_file(
    request: Request,
    file: UploadFile = File(...),
):
    """Uploads a single music file and converts it to ESP32 Opus."""
    require_dashboard_session(request)
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file selected")
        
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format '{ext}'. Allowed audio types: {', '.join(sorted(ALLOWED_AUDIO_EXTENSIONS))}"
        )

    safe_filename = "".join(c for c in file.filename if c.isalnum() or c in "._- ").strip()
    if not safe_filename:
        safe_filename = f"track_{uuid.uuid4().hex[:8]}{ext}"

    dest_path = os.path.join(MUSIC_DIR, safe_filename)
    try:
        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write file to disk: {str(e)}")
    finally:
        await file.close()

    file_size_bytes = os.path.getsize(dest_path)
    file_size_mb = round(file_size_bytes / (1024 * 1024), 2)
    now_iso = datetime.now(timezone.utc).isoformat()

    convert_to_esp32_opus(dest_path)

    notification = {
        "type": "music_uploaded",
        "filename": safe_filename,
        "size_mb": file_size_mb,
        "timestamp": now_iso,
    }
    for q in list(subscribers):
        try:
            q.put_nowait(json.dumps(notification))
        except Exception:
            subscribers.discard(q)

    return {
        "status": "success",
        "message": f"Track '{safe_filename}' uploaded successfully",
        "track": {
            "filename": safe_filename,
            "title": os.path.splitext(safe_filename)[0],
            "extension": ext.replace(".", "").upper(),
            "size_mb": file_size_mb,
            "url": f"/music/{urllib.parse.quote(safe_filename)}",
            "created_at": now_iso,
        }
    }

@router.post("/api/v1/music/upload-chunk", summary="Upload Music Chunk (Bypasses Proxy Limits)")
async def upload_music_chunk(
    request: Request,
    file: UploadFile = File(...),
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    filename: str = Form(...),
):
    """Chunked uploader to support large files through HTTP proxy buffers."""
    require_dashboard_session(request)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported format '{ext}'")

    clean_id = "".join(c for c in upload_id if c.isalnum() or c in "-_")[:64]
    safe_filename = "".join(c for c in filename if c.isalnum() or c in "._- ").strip()
    if not safe_filename:
        safe_filename = f"track_{uuid.uuid4().hex[:8]}{ext}"

    temp_path = os.path.join(MUSIC_DIR, f".tmp_{clean_id}_{safe_filename}")
    
    try:
        mode = "wb" if chunk_index == 0 else "ab"
        with open(temp_path, mode) as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write chunk {chunk_index}: {str(e)}")
    finally:
        await file.close()

    if chunk_index + 1 >= total_chunks:
        dest_path = os.path.join(MUSIC_DIR, safe_filename)
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except Exception:
                pass
        os.rename(temp_path, dest_path)

        file_size_bytes = os.path.getsize(dest_path)
        file_size_mb = round(file_size_bytes / (1024 * 1024), 2)
        now_iso = datetime.now(timezone.utc).isoformat()

        convert_to_esp32_opus(dest_path)

        notification = {
            "type": "music_uploaded",
            "filename": safe_filename,
            "size_mb": file_size_mb,
            "timestamp": now_iso,
        }
        for q in list(subscribers):
            try:
                q.put_nowait(json.dumps(notification))
            except Exception:
                subscribers.discard(q)

        return {
            "status": "success",
            "completed": True,
            "message": f"Track '{safe_filename}' uploaded successfully ({file_size_mb} MB)",
            "track": {
                "filename": safe_filename,
                "title": os.path.splitext(safe_filename)[0],
                "extension": ext.replace(".", "").upper(),
                "size_mb": file_size_mb,
                "url": f"/music/{urllib.parse.quote(safe_filename)}",
                "created_at": now_iso,
            }
        }

    return {
        "status": "success",
        "completed": False,
        "chunk_index": chunk_index,
        "total_chunks": total_chunks,
    }

@router.delete("/api/v1/music/{filename}", summary="Delete Music File")
def delete_music_file(filename: str, request: Request):
    """Deletes an audio file from the vault."""
    require_dashboard_session(request)
    clean_name = os.path.basename(filename)
    target_path = os.path.join(MUSIC_DIR, clean_name)
    
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="Audio file not found")
        
    try:
        os.remove(target_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {str(e)}")

    try:
        with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
            conn.execute("DELETE FROM playlist_tracks WHERE track_filename = ?", (clean_name,))
    except Exception:
        pass

    return {"status": "success", "message": f"Track '{clean_name}' deleted successfully"}

@router.get("/api/v1/playlists", summary="List All Playlists")
def list_playlists(request: Request):
    """Lists playlists and their track items."""
    require_dashboard_session(request)
    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        p_rows = conn.execute("SELECT id, name, description, created_at FROM playlists ORDER BY name ASC").fetchall()
        
        playlists = []
        for p in p_rows:
            t_rows = conn.execute(
                "SELECT track_filename, track_order FROM playlist_tracks WHERE playlist_id = ? ORDER BY track_order ASC",
                (p["id"],)
            ).fetchall()
            
            tracks = []
            for t in t_rows:
                fname = t["track_filename"]
                fpath = os.path.join(MUSIC_DIR, fname)
                if os.path.exists(fpath):
                    tracks.append({
                        "filename": fname,
                        "title": os.path.splitext(fname)[0],
                        "url": f"/music/{urllib.parse.quote(fname)}"
                    })

            playlists.append({
                "id": p["id"],
                "name": p["name"],
                "description": p["description"],
                "created_at": p["created_at"],
                "track_count": len(tracks),
                "tracks": tracks
            })

    return {"status": "success", "count": len(playlists), "playlists": playlists}

@router.post("/api/v1/playlists", status_code=status.HTTP_201_CREATED, summary="Create Playlist")
def create_playlist(payload: PlaylistCreate, request: Request):
    """Creates a new playlist."""
    require_dashboard_session(request)
    p_id = "pl_" + uuid.uuid4().hex[:10]
    now_iso = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        conn.execute(
            "INSERT INTO playlists (id, name, description, created_at) VALUES (?, ?, ?, ?)",
            (p_id, payload.name.strip(), payload.description.strip(), now_iso)
        )
    return {"status": "success", "id": p_id, "name": payload.name.strip()}

@router.post("/api/v1/playlists/{playlist_id}/tracks", summary="Add Track to Playlist")
def add_track_to_playlist(playlist_id: str, payload: PlaylistAddTrack, request: Request):
    """Adds a track to a playlist."""
    require_dashboard_session(request)
    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        p = conn.execute("SELECT id FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
        if not p:
            raise HTTPException(status_code=404, detail="Playlist not found")
            
        clean_name = os.path.basename(payload.filename)
        if not os.path.exists(os.path.join(MUSIC_DIR, clean_name)):
            raise HTTPException(status_code=404, detail="Audio file not found on server")

        entry_id = uuid.uuid4().hex[:12]
        cur_count = conn.execute("SELECT COUNT(*) FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,)).fetchone()[0]
        conn.execute(
            "INSERT INTO playlist_tracks (id, playlist_id, track_filename, track_order) VALUES (?, ?, ?, ?)",
            (entry_id, playlist_id, clean_name, cur_count)
        )
    return {"status": "success", "message": f"Added '{clean_name}' to playlist"}

@router.delete("/api/v1/playlists/{playlist_id}/tracks/{filename}", summary="Remove Track from Playlist")
def remove_track_from_playlist(playlist_id: str, filename: str, request: Request):
    """Removes a track from a playlist."""
    require_dashboard_session(request)
    clean_name = os.path.basename(filename)
    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        conn.execute(
            "DELETE FROM playlist_tracks WHERE playlist_id = ? AND track_filename = ?",
            (playlist_id, clean_name)
        )
    return {"status": "success", "message": f"Removed '{clean_name}' from playlist"}

@router.delete("/api/v1/playlists/{playlist_id}", summary="Delete Playlist")
def delete_playlist(playlist_id: str, request: Request):
    """Deletes a playlist."""
    require_dashboard_session(request)
    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,))
        res = conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Playlist not found")
    return {"status": "success", "message": "Playlist deleted"}

@router.get("/api/v1/music/voice-action", summary="Voice-Triggered Music Resolver for ESP32")
def resolve_voice_music(
    request: Request,
    query: Optional[str] = Query("", description="Song title, playlist, or keyword (e.g., 'relax', 'rock', 'random')"),
    action: Optional[str] = Query("play", description="Action: play, list, random"),
):
    """Voice music resolver for ESP32 voice assistant and web UI."""
    require_dashboard_session(request)
    return voice_music_action(query=query or "", action=action or "play")
