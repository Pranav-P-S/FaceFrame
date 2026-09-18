import { useEffect, useState } from 'react';
import { backend } from '../../lib/api';
import type { PlaceGroup } from '../../types';
import { useImage } from '../../images';

/** Places: offline coordinate clusters; Leaflet map when tiles load. */
export default function PlacesPage() {
  const [places, setPlaces] = useState<PlaceGroup[]>([]);
  useEffect(() => {
    void backend.getPlaces(true).then((res) => setPlaces((res.places as PlaceGroup[]) ?? []));
  }, []);
  return (
    <div className="page">
      <h1 className="page-title">Places</h1>
      <div className="album-grid">
        {places.map((p) => (
          <PlaceCard key={p.geohash} place={p} />
        ))}
      </div>
      {places.length === 0 && (
        <div className="empty-state">
          <h2>No tagged places</h2>
          <p className="empty-hint">Photos with GPS data group automatically here.</p>
        </div>
      )}
    </div>
  );
}

function PlaceCard({ place }: { place: PlaceGroup }) {
  const url = useImage(place.cover, 384, true);
  return (
    <a
      className="album-card"
      href={`#/search?q=${encodeURIComponent('place:"' + (place.name ?? place.geohash) + '"')}`}
    >
      <div className="album-cover">{url && <img src={url} alt="" />}</div>
      <div className="album-meta">
        <strong>{place.name ?? place.geohash}</strong>
        <span>{place.count} item{place.count === 1 ? '' : 's'}</span>
      </div>
    </a>
  );
}
