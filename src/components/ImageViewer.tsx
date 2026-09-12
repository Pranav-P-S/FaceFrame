import { useEffect, useState } from 'react';
import { viewerImage, basename } from '../images';

interface ImageViewerProps {
  images: string[]; // file paths
  index: number;
  onNavigate: (index: number) => void;
  onClose: () => void;
}

export default function ImageViewer({ images, index, onNavigate, onClose }: ImageViewerProps) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setSrc(null);
    setFailed(false);
    viewerImage(images[index]).then((url) => {
      if (cancelled) return;
      if (url) setSrc(url);
      else setFailed(true);
    });
    return () => {
      cancelled = true;
    };
  }, [images, index]);

  const go = (delta: number) => {
    const next = index + delta;
    if (next >= 0 && next < images.length) onNavigate(next);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      if (e.key === 'ArrowRight') go(1);
      if (e.key === 'ArrowLeft') go(-1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, images.length, onClose]);

  return (
    <div className="viewer" role="dialog" aria-modal="true" aria-label="Photo viewer">
      <button className="btn-icon viewer-close" onClick={onClose} aria-label="Close viewer">
        ✕
      </button>
      <div className="viewer-caption">
        {basename(images[index])}
        <span className="viewer-counter">
          {index + 1} / {images.length}
        </span>
      </div>

      {images.length > 1 && (
        <>
          <button
            className="btn-icon viewer-nav viewer-prev"
            onClick={() => go(-1)}
            disabled={index === 0}
            aria-label="Previous photo"
          >
            ‹
          </button>
          <button
            className="btn-icon viewer-nav viewer-next"
            onClick={() => go(1)}
            disabled={index === images.length - 1}
            aria-label="Next photo"
          >
            ›
          </button>
        </>
      )}

      {src ? (
        <img className="viewer-image" src={src} alt={basename(images[index])} draggable={false} />
      ) : failed ? (
        <p className="viewer-missing">This file could not be opened. It may have been moved or deleted.</p>
      ) : (
        <span className="spinner spinner-lg" aria-label="Loading photo" />
      )}
    </div>
  );
}
