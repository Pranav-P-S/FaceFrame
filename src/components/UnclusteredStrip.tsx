import { Face } from '../types';
import LazyImage from './LazyImage';

interface UnclusteredStripProps {
  faces: Face[];
  clustering: boolean;
  onFindPeople: () => void;
  onOpenFace: (face: Face) => void;
}

// Faces that no cluster claimed. They stay visible so nothing silently
// disappears, and the call to action lives right next to them.
export default function UnclusteredStrip({
  faces,
  clustering,
  onFindPeople,
  onOpenFace,
}: UnclusteredStripProps) {
  if (faces.length === 0) return null;

  return (
    <section className="section">
      <div className="section-head">
        <h2>
          Unsorted faces
          <span className="section-sub">{faces.length}</span>
        </h2>
        <button className="btn btn-primary" onClick={onFindPeople} disabled={clustering}>
          {clustering ? (
            <>
              <span className="spinner spinner-inline" /> Finding people…
            </>
          ) : (
            'Find people'
          )}
        </button>
      </div>
      <p className="section-note">
        Faces below have not been matched to anyone yet. Click one to open its photo.
      </p>
      <div className="face-grid">
        {faces.map((face) => (
          <button
            key={face.id}
            className="face-cell"
            onClick={() => onOpenFace(face)}
            title={face.file_path}
          >
            <LazyImage path={face.thumbnail ?? face.file_path} alt="" />
          </button>
        ))}
      </div>
    </section>
  );
}
