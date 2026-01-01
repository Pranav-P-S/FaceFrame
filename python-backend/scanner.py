import os
import hashlib
import logging
from pathlib import Path
from database import Database

logger = logging.getLogger("FaceFrameScanner")

VALID_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tiff'}

class Scanner:
    def __init__(self, db_path: str, processor=None):
        self.db = Database(db_path)
        self.processor = processor

    def calculate_hash(self, file_path: str) -> str:
        """Computes MD5 hash of the file (fast enough for images)."""
        hash_md5 = hashlib.md5()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception as e:
            logger.error(f"Error reading file {file_path}: {e}")
            return ""

    def scan_directory(self, root_path: str, progress_callback=None, abort_check=None):
        logger.info(f"Scanning directory: {root_path}")
        root = Path(root_path)
        
        if not root.exists():
            logger.error(f"Path does not exist: {root_path}")
            return

        # Phase 1: Count total images (for progress bar)
        image_files = []
        for dirpath, _, filenames in os.walk(root):
            for f in filenames:
                if Path(f).suffix.lower() in VALID_EXTENSIONS:
                    image_files.append(os.path.join(dirpath, f))
        
        total_files = len(image_files)
        logger.info(f"Found {total_files} images to process.")
        
        processed_files = 0
        
        for idx, full_path in enumerate(image_files):
             # Progress update
            if progress_callback:
                progress_callback(processed_files, total_files, os.path.basename(full_path))
            
            if abort_check and abort_check():
                logger.info("Scan aborted by user.")
                break

            mtime = os.path.getmtime(full_path)
            
            # Check DB
            if self.db.file_exists(full_path, mtime):
                processed_files += 1
                continue
            
            # Process
            logger.info(f"Processing: {os.path.basename(full_path)}")
            file_hash = self.calculate_hash(full_path)
            
            self.db.add_file(full_path, file_hash, mtime)
            
            if self.processor:
                try:
                    faces = self.processor.process_image(full_path)
                    if faces:
                        self.db.add_faces(full_path, faces) 
                        logger.info(f"Found {len(faces)} faces in {os.path.basename(full_path)}")
                except Exception as e:
                    logger.error(f"Error processing faces for {full_path}: {e}")
            
            processed_files += 1

        logger.info(f"Scan complete. Processed {processed_files} files.")
        if progress_callback:
             progress_callback(processed_files, total_files, "Complete")

