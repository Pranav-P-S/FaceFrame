import logging
import cv2
import numpy as np
import os
from pathlib import Path
import hashlib

try:
    import insightface
    from insightface.app import FaceAnalysis
except ImportError:
    logging.warning("InsightFace not installed yet")
    FaceAnalysis = None

logger = logging.getLogger("FaceFrameProcessor")

class FaceProcessor:
    def __init__(self, use_gpu=True, thumbnail_dir=None):
        if not FaceAnalysis:
            raise ImportError("InsightFace package is missing")
        
        self.thumbnail_dir = thumbnail_dir
        
        # 'providers' argument controls execution provider (CUDA, CoreML, CPU)
        providers = ['CUDAExecutionProvider', 'CPUExecutionProvider'] if use_gpu else ['CPUExecutionProvider']
        
        # Initialize InsightFace model
        # name='buffalo_l' is a good balance of speed/accuracy
        # allowed_modules controls which models to load
        self.app = FaceAnalysis(
            name='buffalo_l', 
            providers=providers,
            allowed_modules=['detection', 'recognition']  # Only load what we need
        )
        # det_size=(640, 640) is default. Larger = better for small faces but slower.
        self.app.prepare(ctx_id=0, det_size=(640, 640)) 
        logger.info(f"FaceProcessor initialized. Providers: {providers}")

    def process_image(self, image_path: str):
        """
        Detects faces in an image.
        Returns a list of dicts with 'embedding', 'bbox', 'thumbnail_path'.
        """
        # Safe read for Windows paths
        try:
            with open(image_path, 'rb') as f:
                file_bytes = np.frombuffer(f.read(), dtype=np.uint8)
                img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        except Exception as e:
            logger.error(f"Failed to read file {image_path}: {e}")
            return []

        if img is None:
            logger.error(f"Could not decode image: {image_path}")
            return []

        faces = self.app.get(img)
        results = []
        
        for idx, face in enumerate(faces):
            bbox = face.bbox.astype(int).tolist()
            x1, y1, x2, y2 = bbox
            
            # Clamp to image bounds
            h, w = img.shape[:2]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            
            # Crop face region
            face_crop = img[y1:y2, x1:x2]
            
            thumbnail_path = None
            if self.thumbnail_dir and face_crop.size > 0:
                # Generate unique ID for this face crop
                face_id = hashlib.md5(f"{image_path}_{idx}_{bbox}".encode()).hexdigest()[:12]
                thumbnail_path = os.path.join(self.thumbnail_dir, f"{face_id}.jpg")
                
                # Resize to thumbnail (96x96 is good for display)
                try:
                    thumb = cv2.resize(face_crop, (96, 96), interpolation=cv2.INTER_AREA)
                    cv2.imwrite(thumbnail_path, thumb, [cv2.IMWRITE_JPEG_QUALITY, 85])
                except Exception as e:
                    logger.warning(f"Failed to save thumbnail: {e}")
                    thumbnail_path = None
            
            results.append({
                'embedding': face.embedding.tolist(), 
                'bbox': bbox,
                'det_score': float(face.det_score),
                'thumbnail': thumbnail_path
            })
            
        if len(results) > 0:
            logger.info(f"Found {len(results)} face(s) in {os.path.basename(image_path)}")
            
        return results

    @staticmethod
    def get_available_providers():
        """Returns list of available ONNX Runtime providers."""
        try:
            import onnxruntime
            return onnxruntime.get_available_providers()
        except ImportError:
            return ["CPUExecutionProvider"]
    
    @staticmethod
    def get_gpu_info():
        """Returns a dict with GPU name and CUDA availability."""
        info = {
            "cuda_available": False,
            "gpu_name": None,
            "cuda_version": None
        }
        try:
            import torch
            if torch.cuda.is_available():
                info["cuda_available"] = True
                info["gpu_name"] = torch.cuda.get_device_name(0)
                info["cuda_version"] = torch.version.cuda
        except ImportError:
            pass
        return info
