import { useEffect, useState } from 'react';
import { backend } from '../../lib/api';
import { useStore } from '../../lib/store';
import type { Item } from '../../types';
import PhotoGrid from '../../components/PhotoGrid';

/** Search results with filter chips derived from the parsed query. */
export default function SearchPage() {
  const route = useStore((s) => s.route);
  const openViewer = useStore((s) => s.openViewer);
  const refreshToken = useStore((s) => s.refreshToken);
  const [items, setItems] = useState<Item[]>([]);
  const [filters, setFilters] = useState<Record<string, string[]>>({});
  const query = route.page === 'search' ? route.query : '';

  useEffect(() => {
    void backend.search(query).then((res) => {
      setItems((res.items as Item[]) ?? []);
      setFilters((res.filters as Record<string, string[]>) ?? {});
    });
  }, [query, refreshToken]);

  return (
    <div className="page">
      <h1 className="page-title">
        {items.length} result{items.length === 1 ? '' : 's'} for “{query || 'everything'}”
      </h1>
      {Object.keys(filters).length > 0 && (
        <div className="chip-row">
          {Object.entries(filters).flatMap(([key, values]) =>
            values.map((v) => (
              <span key={`${key}:${v}`} className="chip">
                <strong>{key}</strong> {v}
              </span>
            ))
          )}
        </div>
      )}
      {items.length > 0 ? (
        <PhotoGrid groups={[{ key: 'all', items }]} view="days" onOpen={openViewer} flatten />
      ) : (
        <div className="empty-state">
          <h2>No matches</h2>
          <p className="empty-hint">Try a person's name, a label like “dog” or “beach”, a year, or filters like type:video is:favorite.</p>
        </div>
      )}
    </div>
  );
}
