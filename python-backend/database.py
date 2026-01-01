import sqlite3
import logging
from pathlib import Path
import json

logger = logging.getLogger("FaceFrameDatabase")

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Files table: Tracks scanned files to skip re-processing
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                hash TEXT,
                modified_time REAL,
                scanned_at REAL
            )
        ''')

        # Persons table: Clusters of faces
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS persons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                thumbnail_path TEXT,
                created_at REAL
            )
        ''')

        # Faces table: Individual detections
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS faces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT,
                embedding BLOB,
                bbox TEXT,
                thumbnail_path TEXT,
                person_id INTEGER,
                FOREIGN KEY(file_path) REFERENCES files(path),
                FOREIGN KEY(person_id) REFERENCES persons(id)
            )
        ''')
        
        conn.commit()
        conn.close()

    def get_connection(self):
        return sqlite3.connect(self.db_path)

    def file_exists(self, path: str, modified_time: float) -> bool:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT modified_time FROM files WHERE path = ?", (path,))
        row = cursor.fetchone()
        conn.close()
        
        if row and row[0] == modified_time:
            return True
        return False

    def add_file(self, path: str, hash_val: str, mtime: float):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO files (path, hash, modified_time, scanned_at)
            VALUES (?, ?, ?, datetime('now'))
        ''', (path, hash_val, mtime))
        conn.commit()
        conn.close()

    def add_faces(self, file_path: str, faces: list):
        """
        Stores detected faces in the 'faces' table.
        faces = [{'embedding': [...], 'bbox': [...], 'thumbnail': '...', ...}, ...]
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        for face in faces:
            emb_json = json.dumps(face['embedding'])
            bbox_json = json.dumps(face['bbox'])
            thumbnail = face.get('thumbnail')
            
            cursor.execute('''
                INSERT INTO faces (file_path, embedding, bbox, thumbnail_path)
                VALUES (?, ?, ?, ?)
            ''', (file_path, emb_json, bbox_json, thumbnail))
            
        conn.commit()
        conn.close()

    def get_unclustered_faces(self):
        """Returns list of (face_id, embedding_bytes) for faces with no person_id"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, embedding FROM faces WHERE person_id IS NULL")
        rows = cursor.fetchall()
        conn.close()
        return rows # [(id, bytes), ...]

    def get_unclustered_faces_info(self):
        """Returns list of (id, file_path, bbox, thumbnail_path) for UI display"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, file_path, bbox, thumbnail_path FROM faces WHERE person_id IS NULL")
        rows = cursor.fetchall()
        conn.close()
        return rows

    def create_person(self, name=None):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO persons (name, created_at) VALUES (?, datetime('now'))", (name,))
        person_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return person_id
        
    def update_face_person(self, face_id, person_id):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE faces SET person_id = ? WHERE id = ?", (person_id, face_id))
        conn.commit()
        conn.close()
        
    def get_persons(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM persons")
        rows = cursor.fetchall()
        conn.close()
        return rows

    def get_photos_by_person(self, person_id: int):
        """Returns list of unique file paths containing this person."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT file_path FROM faces WHERE person_id = ?", (person_id,))
        rows = cursor.fetchall()
        conn.close()
        return [r[0] for r in rows]

    def get_person_face_count(self, person_id: int) -> int:
        """Returns count of faces belonging to a person."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM faces WHERE person_id = ?", (person_id,))
        count = cursor.fetchone()[0]
        conn.close()
        return count

    def rename_person(self, person_id: int, new_name: str):
        """Rename a person."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE persons SET name = ? WHERE id = ?", (new_name, person_id))
        conn.commit()
        conn.close()

    def merge_persons(self, keep_person_id: int, merge_person_id: int):
        """
        Merge two persons: move all faces from merge_person to keep_person,
        then delete merge_person.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        # Move all faces from merge_person to keep_person
        cursor.execute("UPDATE faces SET person_id = ? WHERE person_id = ?", 
                      (keep_person_id, merge_person_id))
        # Delete the merged person
        cursor.execute("DELETE FROM persons WHERE id = ?", (merge_person_id,))
        conn.commit()
        conn.close()

    def update_person_thumbnail(self, person_id: int, thumbnail_path: str):
        """Set thumbnail for a person."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE persons SET thumbnail_path = ? WHERE id = ?", 
                      (thumbnail_path, person_id))
        conn.commit()
        conn.close()

    def get_first_face_thumbnail(self, person_id: int):
        """Get the thumbnail path of the first face for a person."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT thumbnail_path FROM faces WHERE person_id = ? AND thumbnail_path IS NOT NULL LIMIT 1",
            (person_id,)
        )
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None

