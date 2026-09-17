import { useEffect, useState } from 'react';
import type { MemoryGroup } from '../types';
import { useImage } from '../images';
import { useStore } from '../lib/store';

export function MemoriesBar({ memories }: { memories: MemoryGroup[] }) {
  const [playing, setPlaying] = useState<MemoryGroup | null>(null);
  if (!memories.length) return null;
  return (
    <>
      <div className="memories">
        {memories.map((m) => (
          <MemoryCard key={m.title} memory={m} onPlay={() => setPlaying(m)} />
        ))}
      </div>
      {playing && <MemoryPlayer memory={playing} onClose={() => setPlaying(null)} />}
    </>
  );
}

function MemoryCard({ memory, onPlay }: { memory: MemoryGroup; onPlay: () => void }) {
  const url = useImage(memory.cover, 384, false);
  return (
    <button className="memory-card" onClick={onPlay}>
      {url && <img src={url} alt="" />}
      <span className="memory-title">{memory.title}</span>
    </button>
  );
}

export function MemoryPlayer({ memory, onClose }: { memory: MemoryGroup; onClose: () => void }) {
  const [index, setIndex] = useState(0);
  const item = memory.items[index];

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      else if (e.key === 'ArrowRight') next();
      else if (e.key === 'ArrowLeft') setIndex((i) => Math.max(0, i - 1));
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  function next() {
    if (index < memory.items.length - 1) setIndex(index + 1);
    else onClose();
  }

  const url = useImage(item?.path, 2200, false);
  void useStore; // player is chrome-free by design

  return (
    <div className="memory-player" role="dialog" aria-modal="true" onClick={next}>
      <div className="memory-progress">
        {memory.items.map((_, i) => (
          <span key={i} className={`memory-seg ${i <= index ? 'memory-seg-done' : ''}`} />
        ))}
      </div>
      {url && <img src={url} alt="" />}
      <div className="memory-caption">
        <strong>{memory.title}</strong>
        <span>{index + 1} of {memory.items.length} · click or → to continue, Esc to close</span>
      </div>
    </div>
  );
}
