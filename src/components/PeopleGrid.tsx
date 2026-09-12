import { useEffect, useRef, useState } from 'react';
import { Person } from '../types';
import { useImage } from '../images';

interface PersonCardProps {
  person: Person;
  mergeState: 'none' | 'source' | 'target';
  busy: boolean;
  onOpen: () => void;
  onRename: (name: string) => void;
  onStartMerge: () => void;
  onCancelMerge: () => void;
}

function Avatar({ person }: { person: Person }) {
  const src = useImage(person.thumbnail);
  return (
    <div className="avatar">
      {src && src !== 'failed' ? (
        <img src={src} alt={person.name} draggable={false} />
      ) : (
        <span aria-hidden>{person.name?.charAt(0)?.toUpperCase() || '?'}</span>
      )}
    </div>
  );
}

function PersonCard({
  person,
  mergeState,
  busy,
  onOpen,
  onRename,
  onStartMerge,
  onCancelMerge,
}: PersonCardProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(person.name);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) {
      setDraft(person.name);
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing, person.name]);

  const commit = () => {
    const name = draft.trim();
    if (name && name !== person.name) onRename(name);
    setEditing(false);
  };

  const isMergeSource = mergeState === 'source';
  const isMergeTarget = mergeState === 'target';

  return (
    <div
      className={`person-card${isMergeSource ? ' merge-source' : ''}${
        isMergeTarget ? ' merge-target' : ''
      }`}
      onClick={editing || busy ? undefined : onOpen}
      onKeyDown={
        editing || busy
          ? undefined
          : (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                onOpen();
              }
            }
      }
      role="button"
      tabIndex={editing || busy ? -1 : 0}
      aria-label={`Open ${person.name}`}
    >
      <Avatar person={person} />

      {editing ? (
        <div className="person-edit" onClick={(e) => e.stopPropagation()}>
          <input
            ref={inputRef}
            value={draft}
            maxLength={60}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commit();
              if (e.key === 'Escape') setEditing(false);
            }}
            aria-label="Person name"
          />
          <div className="person-edit-actions">
            <button className="btn btn-small" onClick={commit}>
              Save
            </button>
            <button className="btn btn-small" onClick={() => setEditing(false)}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <>
          <div className="person-name" title={person.name}>
            {person.name}
          </div>
          <div className="person-count">
            {person.face_count} {person.face_count === 1 ? 'face' : 'faces'}
          </div>
          <div
            className="person-actions"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}
          >
            <button
              className="btn btn-small"
              disabled={busy}
              onClick={() => setEditing(true)}
            >
              Rename
            </button>
            {isMergeSource ? (
              <button className="btn btn-small" disabled={busy} onClick={onCancelMerge}>
                Cancel
              </button>
            ) : (
              <button
                className="btn btn-small"
                disabled={busy || isMergeTarget}
                onClick={onStartMerge}
                title="Combine this card with another person"
              >
                Merge
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}

interface PeopleGridProps {
  persons: Person[];
  busy: boolean;
  onOpenPerson: (person: Person) => void;
  onRename: (person: Person, name: string) => void;
  onMerge: (keep: Person, merge: Person) => void;
}

export default function PeopleGrid({
  persons,
  busy,
  onOpenPerson,
  onRename,
  onMerge,
}: PeopleGridProps) {
  const [mergeSource, setMergeSource] = useState<Person | null>(null);

  useEffect(() => {
    if (mergeSource && !persons.some((p) => p.id === mergeSource.id)) {
      setMergeSource(null);
    }
  }, [persons, mergeSource]);

  if (persons.length === 0) return null;

  return (
    <section className="section">
      <div className="section-head">
        <h2>People</h2>
        {mergeSource && (
          <div className="merge-hint">
            Pick the card to merge <strong>{mergeSource.name}</strong> into
            <button className="btn btn-small" onClick={() => setMergeSource(null)}>
              Stop
            </button>
          </div>
        )}
      </div>
      <div className="people-grid">
        {persons.map((person) => (
          <PersonCard
            key={person.id}
            person={person}
            busy={busy}
            mergeState={
              mergeSource?.id === person.id
                ? 'source'
                : mergeSource
                  ? 'target'
                  : 'none'
            }
            onOpen={() => {
              if (mergeSource && mergeSource.id !== person.id) {
                onMerge(mergeSource, person);
                setMergeSource(null);
              } else if (!mergeSource) {
                onOpenPerson(person);
              }
            }}
            onRename={(name) => onRename(person, name)}
            onStartMerge={() => setMergeSource(person)}
            onCancelMerge={() => setMergeSource(null)}
          />
        ))}
      </div>
    </section>
  );
}
