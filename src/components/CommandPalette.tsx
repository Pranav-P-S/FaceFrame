import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../types';
import { useStore, type Route } from '../lib/store';

export interface Command {
  id: string;
  title: string;
  keywords: string;
  section: string;
  run: () => void;
}

/** Subsequence match with consecutive-run bonus — no dependency. */
export function scoreCommand(query: string, text: string): number {
  if (!query) return 1;
  const q = query.toLowerCase();
  const t = text.toLowerCase();
  let qi = 0;
  let score = 0;
  let streak = 0;
  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (t[ti] === q[qi]) {
      qi++;
      streak += 1;
      score += 2 + streak; // consecutive runs score higher
    } else {
      streak = 0;
    }
  }
  return qi === q.length ? score : 0;
}

export function buildCommands(
  navigate: (r: Route) => void,
  libraryPath: string | null,
  _refresh?: () => void,
): Command[] {
  const go = (page: Route['page'], title: string, keywords: string): Command => ({
    id: `go:${page}`,
    title,
    keywords,
    section: 'Go to',
    run: () => navigate({ page } as Route),
  });
  const cmds: Command[] = [
    go('photos', 'Photos', 'feed home grid'),
    go('explore', 'Explore', 'people places things'),
    go('albums', 'Albums', 'collections'),
    go('people', 'People', 'faces persons'),
    go('places', 'Places', 'map locations gps'),
    go('archive', 'Archive', 'hidden'),
    go('trash', 'Trash', 'deleted recycle bin'),
    go('locked', 'Locked folder', 'private hidden passcode'),
    go('utilities', 'Utilities', 'duplicates missing storage health backup'),
    go('settings', 'Settings', 'preferences engine watch'),
  ];
  cmds.push({
    id: 'action:scan',
    title: 'Scan library now',
    keywords: 'rescan index refresh import',
    section: 'Actions',
    run: () => {
      if (!libraryPath) return;
      void api().scanDirectory(libraryPath, 'auto');
    },
  });
  cmds.push({
    id: 'action:cluster',
    title: 'Find people (cluster faces)',
    keywords: 'faces group recognize',
    section: 'Actions',
    run: () => {
      if (!libraryPath) return;
      void api().clusterFaces(libraryPath);
    },
  });
  cmds.push({
    id: 'action:theme',
    title: 'Toggle light/dark theme',
    keywords: 'dark light appearance mode',
    section: 'Actions',
    run: () => {
      const cur = document.documentElement.dataset.theme;
      useStore.getState().setTheme(cur === 'light' ? 'dark' : 'light');
    },
  });
  return cmds;
}

export default function CommandPalette({ onClose }: { onClose: () => void }) {
  const navigate = useStore((s) => s.navigate);
  const libraryPath = useStore((s) => s.libraryPath);
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const commands = useMemo(
    () => buildCommands(navigate, libraryPath, () => undefined),
    [navigate, libraryPath]
  );

  const results = useMemo(() => {
    const scored = commands
      .map((c) => ({ c, s: Math.max(scoreCommand(query, c.title), scoreCommand(query, c.keywords)) }))
      .filter((x) => x.s > 0)
      .sort((a, b) => b.s - a.s);
    if (query.trim()) {
      scored.push({
        c: {
          id: 'search',
          title: `Search for “${query.trim()}”`,
          keywords: 'search find',
          section: 'Search',
          run: () => navigate({ page: 'search', query: query.trim() }),
        },
        s: 1,
      });
    }
    return scored.map((x) => x.c);
  }, [commands, query, navigate]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);
  useEffect(() => setSelected(0), [query]);

  const runAt = (i: number) => {
    const cmd = results[i];
    if (!cmd) return;
    onClose();
    cmd.run();
  };

  return (
    <div className="palette-backdrop" role="dialog" aria-modal="true" onMouseDown={onClose}>
      <div className="palette" onMouseDown={(e) => e.stopPropagation()}>
        <input
          ref={inputRef}
          className="palette-input"
          placeholder="Type a command or search…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') onClose();
            else if (e.key === 'ArrowDown') { e.preventDefault(); setSelected((s) => Math.min(s + 1, results.length - 1)); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); setSelected((s) => Math.max(s - 1, 0)); }
            else if (e.key === 'Enter') runAt(selected);
          }}
        />
        <div className="palette-list">
          {results.map((cmd, i) => (
            <button
              key={cmd.id}
              className={`palette-item ${i === selected ? 'palette-item-active' : ''}`}
              onMouseEnter={() => setSelected(i)}
              onClick={() => runAt(i)}
            >
              <span>{cmd.title}</span>
              <span className="palette-section">{cmd.section}</span>
            </button>
          ))}
          {results.length === 0 && <div className="palette-empty">No matching commands</div>}
        </div>
        <div className="palette-hint">↑↓ navigate · Enter run · Esc close</div>
      </div>
    </div>
  );
}
