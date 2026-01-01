
interface Face {
    id: number;
    file_path: string;
    bbox: [number, number, number, number]; // x1, y1, x2, y2
    thumbnail?: string; // Path to thumbnail image
}

interface UnclusteredFacesProps {
    faces: Face[];
    onFaceClick?: (face: Face) => void;
}

// Helper to convert Windows paths to safe-file:// URLs
function toSafeFileUrl(path: string | undefined): string | null {
    if (!path) return null;
    // Replace backslashes with forward slashes for URL compatibility
    let normalized = path.replace(/\\/g, '/');
    return `safe-file://${normalized}`;
}

const FaceItem = ({ face, onClick }: { face: Face; onClick?: () => void }) => {
    const imgSrc = toSafeFileUrl(face.thumbnail);

    return (
        <div
            onClick={onClick}
            style={{
                width: '96px',
                height: '96px',
                overflow: 'hidden',
                borderRadius: '8px',
                margin: '5px',
                background: '#333',
                cursor: onClick ? 'pointer' : 'default',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                border: '2px solid transparent',
                transition: 'border-color 0.2s'
            }}
            onMouseEnter={(e) => e.currentTarget.style.borderColor = '#2196F3'}
            onMouseLeave={(e) => e.currentTarget.style.borderColor = 'transparent'}
        >
            {imgSrc ? (
                <img
                    src={imgSrc}
                    style={{
                        width: '100%',
                        height: '100%',
                        objectFit: 'cover'
                    }}
                    onError={(e) => {
                        console.error('Failed to load face thumbnail:', imgSrc);
                        // Show placeholder instead of broken image
                        const target = e.target as HTMLImageElement;
                        target.style.display = 'none';
                        if (target.parentElement) {
                            target.parentElement.innerHTML = '<span style="color:#666;font-size:12px">No Image</span>';
                        }
                    }}
                />
            ) : (
                <span style={{ color: '#666', fontSize: '12px' }}>No Image</span>
            )}
        </div>
    );
};

export default function UnclusteredFaces({ faces, onFaceClick }: UnclusteredFacesProps) {
    if (faces.length === 0) {
        return (
            <div style={{ padding: '20px', textAlign: 'center', color: '#666' }}>
                <p>No faces detected yet. Scan a folder to find faces.</p>
            </div>
        );
    }

    return (
        <div style={{ width: '100%', padding: '20px' }}>
            <h3 style={{ borderBottom: '1px solid #444', paddingBottom: '10px', marginBottom: '15px' }}>
                Detected Faces ({faces.length})
            </h3>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '5px' }}>
                {faces.map(f => (
                    <FaceItem
                        key={f.id}
                        face={f}
                        onClick={onFaceClick ? () => onFaceClick(f) : undefined}
                    />
                ))}
            </div>
        </div>
    );
}
