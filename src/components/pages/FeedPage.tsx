import { useEffect, useState } from 'react';
import { backend } from '../../lib/api';
import { useStore } from '../../lib/store';
import type { FeedGroup, MemoryGroup } from '../../types';
import PhotoGrid from '../../components/PhotoGrid';
import { MemoriesBar } from '../../components/Memory';

/** Main feed: memories carousel + the day-grouped grid. Grid density maps to
 * GP's continuous zoom: 0-1 days, 2 months, 3 years. */
export default function FeedPage() {
  const density = useStore((s) => s.density);
  const openViewer = useStore((s) => s.openViewer);
  const refreshToken = useStore((s) => s.refreshToken);
  const [groups, setGroups] = useState<FeedGroup[]>([]);
  const [memories, setMemories] = useState<MemoryGroup[]>([]);
  const showDays = density < 2 ? 'days' : density === 2 ? 'months' : 'years';

  useEffect(() => {
    void backend.getFeed({ view: showDays }).then((res) => {
      setGroups((res.groups as FeedGroup[]) ?? []);
    });
    void backend.getMemories().then((res) => setMemories((res.memories as MemoryGroup[]) ?? []));
  }, [showDays, refreshToken]);

  return (
    <div className="page">
      {memories.length > 0 && <MemoriesBar memories={memories} />}
      {groups.length > 0 ? (
        <PhotoGrid groups={groups} view={showDays} onOpen={openViewer} />
      ) : (
        <div className="empty-state">
          <h2>No photos here yet</h2>
          <p className="empty-hint">Pick your library folder to start indexing — everything stays on this machine.</p>
        </div>
      )}
    </div>
  );
}
