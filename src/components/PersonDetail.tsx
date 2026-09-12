import { useEffect } from 'react';
import { Person } from '../types';
import LazyImage from './LazyImage';

interface PersonDetailProps {
  person: Person;
  photos: { path: string; face_count: number }[] | null;
  onBack: () => void;
  onOpenPhoto: (path: string) => void;
}

export default function PersonDetail({
  person,
  photos,
  onBack,
  onOpenPhoto,
}: PersonDetailProps) {
  // Esc climbs back to the grid, like the viewer and dialogs.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onBack();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onBack]);

  return (
    <section className="section person-detail">
      <div className="section-head">
        <button className="btn" onClick={onBack}>
          ← All people
        </button>
        <h2>
          {person.name}
          {photos && (
            <span className="section-sub">
              {photos.length} {photos.length === 1 ? 'photo' : 'photos'}
            </span>
          )}
        </h2>
      </div>

      {photos === null ? (
        <div className="empty-hint">
          <span className="spinner" aria-label="loading" />
        </div>
      ) : photos.length === 0 ? (
        <p className="empty-hint">No photos reference this person.</p>
      ) : (
        <div className="photo-grid">
          {photos.map((photo) => (
            <button
              key={photo.path}
              className="photo-cell"
              onClick={() => onOpenPhoto(photo.path)}
              title={photo.path}
            >
              <LazyImage path={photo.path} alt="" />
              {photo.face_count > 1 && (
                <span
                  className="photo-badge"
                  title={`This person appears ${photo.face_count} times in this photo`}
                >
                  {photo.face_count}×
                </span>
              )}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
