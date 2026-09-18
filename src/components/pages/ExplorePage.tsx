import { useEffect, useState } from 'react';
import { backend } from '../../lib/api';
import { useStore } from '../../lib/store';
import type { Item, Person, PlaceGroup } from '../../types';
import { useImage } from '../../images';

/** Explore: people, places and things — the auto-organized projections. */
export default function ExplorePage() {
  const libraryPath = useStore((s) => s.libraryPath);
  const [persons, setPersons] = useState<Person[]>([]);
  const [places, setPlaces] = useState<PlaceGroup[]>([]);
  const [things, setThings] = useState<{ label: string; count: number; cover: Item | null }[]>([]);

  useEffect(() => {
    if (!libraryPath) return;
    void backend.getPersons(libraryPath).then((res) => setPersons((res.persons as Person[]) ?? []));
    void backend.getPlaces(false).then((res) => setPlaces((res.places as PlaceGroup[]) ?? []));
    void backend.search('').then((res) => {
      const items = (res.items as Item[]) ?? [];
      const byLabel = new Map<string, { count: number; cover: Item | null }>();
      for (const item of items) {
        const itemLabels = (item.labels ?? []) as string[];
        for (const label of itemLabels) {
          const entry = byLabel.get(label) ?? { count: 0, cover: null };
          if (!entry.cover) entry.cover = item;
          entry.count += 1;
          byLabel.set(label, entry);
        }
      }
      setThings([...byLabel.entries()].sort((a, b) => b[1].count - a[1].count).slice(0, 12).map(([label, v]) => ({ label, ...v })));
    });
  }, [libraryPath]);

  return (
    <div className="page">
      <h1 className="page-title">Explore</h1>

      <h2 className="section-title">People</h2>
      <div className="people-grid">
        {persons.slice(0, 8).map((p) => (
          <PersonChip key={p.id} person={p} />
        ))}
      </div>

      <h2 className="section-title">Places</h2>
      <div className="album-grid">
        {places.slice(0, 6).map((p) => (
          <PlaceTile key={p.geohash} place={p} />
        ))}
      </div>

      <h2 className="section-title">Things</h2>
      <div className="chip-row">
        {things.map((t) => (
          <a key={t.label} className="chip chip-link" href={`#/search?q=${encodeURIComponent(t.label)}`}>
            {t.label} <span className="info-muted">· {t.count}</span>
          </a>
        ))}
      </div>
      {things.length === 0 && (
        <p className="info-muted">Enable “Things labels” in Settings, then rescan to search by what's in your photos.</p>
      )}
    </div>
  );
}

function PersonChip({ person }: { person: Person }) {
  const url = useImage(person.thumbnail, 384, true);
  return (
    <a className="person-card" href={`#/people/${person.id}`}>
      <div className="person-avatar">{url && <img src={url} alt="" />}</div>
      <strong>{person.name}</strong>
    </a>
  );
}

function PlaceTile({ place }: { place: PlaceGroup }) {
  const url = useImage(place.cover, 384, true);
  return (
    <a className="album-card" href="#/places">
      <div className="album-cover">{url && <img src={url} alt="" />}</div>
      <div className="album-meta"><strong>{place.name ?? place.geohash}</strong></div>
    </a>
  );
}
