import numpy as np
from sklearn.cluster import DBSCAN
import logging
import json
from database import Database

logger = logging.getLogger("FaceFrameClusterer")

class Clusterer:
    def __init__(self, db_path: str):
        self.db = Database(db_path)

    def run_clustering(self):
        logger.info("Starting clustering...")
        
        # 1. Fetch unclustered faces
        rows = self.db.get_unclustered_faces() # [(id, embedding_json), ...]
        if not rows:
            logger.info("No unclustered faces found.")
            return 0
            
        face_ids = []
        embeddings = []
        
        for r in rows:
            face_ids.append(r[0])
            # Embedding is stored as JSON string, parse it
            try:
                emb_data = r[1]
                if isinstance(emb_data, str):
                    emb = np.array(json.loads(emb_data), dtype=np.float32)
                elif isinstance(emb_data, bytes):
                    emb = np.array(json.loads(emb_data.decode('utf-8')), dtype=np.float32)
                else:
                    logger.warning(f"Unknown embedding type for face {r[0]}: {type(emb_data)}")
                    continue
                embeddings.append(emb)
            except Exception as e:
                logger.error(f"Failed to parse embedding for face {r[0]}: {e}")
                continue
            
        if len(embeddings) < 2:
            logger.info("Not enough valid embeddings for clustering (need at least 2).")
            return 0

        X = np.array(embeddings)
        
        # Normalize embeddings for better clustering (InsightFace embeddings should already be normalized)
        norms = np.linalg.norm(X, axis=1, keepdims=True)
        norms[norms == 0] = 1  # Prevent division by zero
        X = X / norms
        
        # 2. Run DBSCAN
        # eps=0.5 -> cosine similarity > ~0.87 for normalized vectors
        # min_samples=2 for small datasets (was 3)
        clustering = DBSCAN(eps=0.5, min_samples=2, metric="euclidean", n_jobs=-1).fit(X)
        labels = clustering.labels_
        
        # 3. Process results
        # labels: -1 = noise, 0..N = cluster ID
        
        cluster_map = {} # label_id -> db_person_id
        
        new_people_count = 0
        assigned_count = 0
        
        unique_labels = set(labels)
        
        for label in unique_labels:
            if label == -1:
                continue
                
            # Create new person for this cluster
            person_name = f"Person {label + 1}"
            person_id = self.db.create_person(name=person_name)
            cluster_map[label] = person_id
            new_people_count += 1
            
            # Set first face as thumbnail for this person
            first_face_idx = list(labels).index(label)
            # TODO: Get thumbnail path from face and update person
            
        # 4. Update Faces
        for idx, label in enumerate(labels):
            if label != -1:
                person_id = cluster_map[label]
                face_id = face_ids[idx]
                self.db.update_face_person(face_id, person_id)
                assigned_count += 1
        
        # 5. Set thumbnail for each person from their first face
        for label, person_id in cluster_map.items():
            thumbnail = self.db.get_first_face_thumbnail(person_id)
            if thumbnail:
                self.db.update_person_thumbnail(person_id, thumbnail)
                
        logger.info(f"Clustering complete. Created {new_people_count} people. Assigned {assigned_count} faces.")
        return new_people_count
