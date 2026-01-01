import sys
import json
import logging
import threading
import os
from pathlib import Path

# Add current directory to path so relative imports work if running from root
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger("FaceFrameBackend")

import database
import scanner
import processor
from database import Database
from scanner import Scanner
from processor import FaceProcessor

# Global State
processor_instance = None
scanner_instance = None

# Global Abort Flag
abort_scan_flag = False

def run_scan(path, provider_name="CPUExecutionProvider"):
    global processor_instance, scanner_instance, abort_scan_flag
    
    # Reset abort flag
    abort_scan_flag = False
    
    try:
        # Create directories
        faceframe_dir = Path(path) / ".faceframe"
        faceframe_dir.mkdir(parents=True, exist_ok=True)
        
        thumbnail_dir = faceframe_dir / "thumbnails"
        thumbnail_dir.mkdir(parents=True, exist_ok=True)
        
        db_path = faceframe_dir / "index.db"
        
        # HACK: If provider is CUDA, use_gpu=True. Else False.
        use_gpu = 'CUDA' in provider_name
        processor_instance = FaceProcessor(use_gpu=use_gpu, thumbnail_dir=str(thumbnail_dir))

        scanner_instance = Scanner(str(db_path), processor_instance)
        
        def on_progress(current, total, filename):
             print(json.dumps({
                "status": "progress", 
                "current": current, 
                "total": total, 
                "file": filename
            }), flush=True)

        print(json.dumps({"status": "started", "path": path}), flush=True)
        
        scanner_instance.scan_directory(path, progress_callback=on_progress, abort_check=lambda: abort_scan_flag)
        
        if abort_scan_flag:
            print(json.dumps({"status": "cancelled", "message": "Scan cancelled by user."}), flush=True)
        else:
            print(json.dumps({"status": "complete", "path": path}), flush=True)
        
    except Exception as e:
        logger.error(f"Scan error: {e}")
        print(json.dumps({"status": "error", "message": str(e)}), flush=True)

def main():
    logger.info("FaceFrame Python Backend Started")
    
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            
            command_str = line.strip()
            if not command_str:
                continue

            try:
                cmd = json.loads(command_str)
                action = cmd.get('action')
                
                if action == 'SCAN':
                    path = cmd.get('path')
                    provider = cmd.get('provider', 'CPUExecutionProvider')
                    if path:
                        t = threading.Thread(target=run_scan, args=(path, provider))
                        t.start()
                
                elif action == 'GET_PROVIDERS':
                    import onnxruntime
                    providers = onnxruntime.get_available_providers()
                    
                    # Also get GPU info for friendlier display
                    gpu_info = FaceProcessor.get_gpu_info()
                    
                    print(json.dumps({
                        "status": "providers", 
                        "providers": providers,
                        "gpu_info": gpu_info
                    }), flush=True)

                elif action == 'CLUSTER':
                    path = cmd.get('path')
                    if path:
                        from clusterer import Clusterer
                        db_path = Path(path) / ".faceframe" / "index.db"
                        
                        clusterer = Clusterer(str(db_path))
                        count = clusterer.run_clustering()
                        print(json.dumps({"status": "clustered", "count": count}), flush=True)

                elif action == 'GET_UNCLUSTERED':
                    path = cmd.get('path')
                    if path:
                        db_path = Path(path) / ".faceframe" / "index.db"
                        db = Database(str(db_path))
                        faces = db.get_unclustered_faces_info()
                        # Convert to list of dicts - now includes thumbnail path
                        data = []
                        for f in faces:
                            face_data = {
                                "id": f[0], 
                                "file_path": f[1], 
                                "bbox": json.loads(f[2]) if f[2] else [0,0,0,0]
                            }
                            # Check for thumbnail (if stored in DB)
                            if len(f) > 3 and f[3]:
                                face_data["thumbnail"] = f[3]
                            data.append(face_data)
                        print(json.dumps({"status": "unclustered", "data": data}), flush=True)

                elif action == 'CANCEL_SCAN':
                    global abort_scan_flag
                    abort_scan_flag = True
                    logger.info("Cancel signal received.") 

                elif action == 'CLEAR_INDEX':
                    path = cmd.get('path')
                    if path:
                        import shutil
                        folder = Path(path) / ".faceframe"
                        if folder.exists():
                            shutil.rmtree(folder)
                        print(json.dumps({"status": "index_cleared"}), flush=True)

                elif action == 'GET_PERSONS':
                    path = cmd.get('path')
                    if path:
                        db_path = Path(path) / ".faceframe" / "index.db"
                        db = Database(str(db_path))
                        persons = db.get_persons() # [(id, name, thumb, date), ...]
                        # Also get face count per person
                        data = []
                        for p in persons:
                            person_data = {
                                "id": p[0], 
                                "name": p[1] or f"Person {p[0]}", 
                                "thumbnail": p[2]
                            }
                            # Get count of faces for this person
                            data.append(person_data)
                        print(json.dumps({"status": "persons", "data": data}), flush=True)
                
                elif action == 'GET_PHOTOS_BY_PERSON':
                    # Get all file_paths containing a specific person
                    path = cmd.get('path')
                    person_id = cmd.get('person_id')
                    if path and person_id:
                        db_path = Path(path) / ".faceframe" / "index.db"
                        db = Database(str(db_path))
                        photos = db.get_photos_by_person(person_id)
                        print(json.dumps({"status": "photos_by_person", "person_id": person_id, "photos": photos}), flush=True)

                elif action == 'RENAME_PERSON':
                    path = cmd.get('path')
                    person_id = cmd.get('person_id')
                    new_name = cmd.get('new_name')
                    if path and person_id and new_name:
                        db_path = Path(path) / ".faceframe" / "index.db"
                        db = Database(str(db_path))
                        db.rename_person(person_id, new_name)
                        print(json.dumps({"status": "person_renamed", "person_id": person_id, "new_name": new_name}), flush=True)

                elif action == 'MERGE_PERSONS':
                    path = cmd.get('path')
                    keep_id = cmd.get('keep_id')
                    merge_id = cmd.get('merge_id')
                    if path and keep_id and merge_id:
                        db_path = Path(path) / ".faceframe" / "index.db"
                        db = Database(str(db_path))
                        db.merge_persons(keep_id, merge_id)
                        print(json.dumps({"status": "persons_merged", "keep_id": keep_id, "merge_id": merge_id}), flush=True)

                elif action == 'PING':
                    print(json.dumps({"status": "pong"}), flush=True)

            except json.JSONDecodeError:
                logger.error("Invalid JSON")
                
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Main loop error: {e}")

if __name__ == "__main__":
    main()
