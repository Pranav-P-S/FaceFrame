# FaceFrame

A local, privacy-focused face detection and photo organization application. Automatically detect faces in your photos, cluster them by person, and organize your photo library.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)

## Features

- 🔍 **Face Detection** - Automatically detect faces in your photos using InsightFace
- 👥 **Face Clustering** - Group similar faces together to identify people
- 🖼️ **Photo Organization** - Browse your photos organized by person
- 🔒 **Privacy First** - Everything runs locally, your photos never leave your device
- ⚡ **GPU Accelerated** - Uses NVIDIA CUDA for fast processing (CPU fallback available)

## Screenshots

*Coming soon*

## Requirements

- Windows 10/11
- Node.js 18+
- Python 3.10+
- NVIDIA GPU with CUDA 12.1 support (optional, for GPU acceleration)

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/yourusername/FaceFrame.git
cd FaceFrame
```

### 2. Install Node.js dependencies

```bash
npm install
```

### 3. Set up Python environment

```bash
python -m venv venv
.\venv\Scripts\activate  # Windows
pip install -r python-backend/requirements.txt
```

### 4. Install PyTorch with CUDA (optional, for GPU acceleration)

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
```

## Usage

### Development Mode

```bash
npm run electron:dev
```

### Build for Production

```bash
npm run electron:build
```

## Project Structure

```
FaceFrame/
├── electron/           # Electron main process
│   ├── main.cjs       # Main process entry point
│   └── preload.cjs    # Preload script for IPC
├── python-backend/     # Python face detection backend
│   ├── main.py        # Backend entry point
│   ├── processor.py   # Face detection using InsightFace
│   ├── database.py    # SQLite database management
│   ├── scanner.py     # Directory scanning and file processing
│   └── clusterer.py   # Face clustering using DBSCAN
├── src/               # React frontend
│   ├── App.tsx        # Main application component
│   └── components/    # React components
└── package.json
```

## How It Works

1. **Scan**: Select a folder containing photos. FaceFrame scans for images and detects faces using InsightFace.
2. **Index**: Detected faces are saved as thumbnails in `.faceframe/thumbnails/` and stored in a local SQLite database.
3. **Cluster**: Click "Find People" to group similar faces using DBSCAN clustering.
4. **Organize**: Browse your photos organized by person. Rename people and merge duplicates.

## Technology Stack

- **Frontend**: React, TypeScript, Vite
- **Desktop**: Electron
- **Backend**: Python, InsightFace, ONNX Runtime
- **Database**: SQLite, FAISS
- **ML**: InsightFace (buffalo_l model), scikit-learn DBSCAN

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- [InsightFace](https://github.com/deepinsight/insightface) for face detection and recognition
- [ONNX Runtime](https://onnxruntime.ai/) for model inference
- [Electron](https://www.electronjs.org/) for cross-platform desktop support
