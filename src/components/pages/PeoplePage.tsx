import { useCallback, useEffect, useState } from 'react';
import { api, type Face, type Item, type Person } from '../../types';
import { backend } from '../../lib/api';
import { useStore } from '../../lib/store';
import PhotoGrid from '../../components/PhotoGrid';
import { promptText } from '../../lib/prompt';
import { useImage } from '../../images';

/** People grid or one person's photos depending on the route. */
export default function PeoplePage() {
  const route = useStore((s) => s.route);
  const libraryPath = useStore((s) => s.libraryPath);
  const refreshToken = useStore((s) => s.refreshToken);
  const [persons, setPersons] = useState<Person[]>([]);
  const [hiddenPersons, setHiddenPersons] = useState<Person[]>([]);
  const [unclustered, setUnclustered] = useState<Face[]>([]);
  const [clustering, setClustering] = useState(false);
  const showToast = useStore((s) => s.showToast);

  const refresh = useCallback(() => {
    if (!libraryPath) return;
    // include_hidden so hidden people stay reachable; the grid filters.
    void backend.getPersons(libraryPath).then((res) => {
      const all = (res.persons as Person[]) ?? [];
      setPersons(all.filter((p) => !p.hidden));
      setHiddenPersons(all.filter((p) => p.hidden));
    });
    void backend.getUnclustered(libraryPath).then((res) => setUnclustered((res.faces as Face[]) ?? []));
  }, [libraryPath]);

  useEffect(() => {
    refresh();
  }, [refresh, refreshToken]);

  if (route.page === 'people' && route.personId != null) {
    return <PersonDetail personId={route.personId} />;
  }

  const findPeople = async () => {
    if (!libraryPath) return;
    setClustering(true);
    try {
      await api().clusterFaces(libraryPath);
      showToast({ text: 'Face grouping updated', kind: 'info' });
    } catch {
      showToast({ text: 'Face grouping failed', kind: 'error' });
    } finally {
      setClustering(false);
      refresh();
    }
  };

  const assignFace = async (faceId: number, personId: number | null) => {
    if (!libraryPath) return;
    try {
      await backend.assignFaces(libraryPath, [faceId], personId);
      showToast({
        text: personId != null ? 'Face moved to the person' : 'Face left ungrouped',
        kind: 'info',
      });
    } catch {
      showToast({ text: 'Could not assign that face', kind: 'error' });
    }
    refresh();
  };

  const unhide = async (person: Person) => {
    if (!libraryPath) return;
    await backend.setPersonHidden(libraryPath, person.id, false);
    refresh();
  };

  return (
    <div className="page">
      <div className="page-head">
        <h1 className="page-title">People</h1>
        <button className="btn" onClick={() => void findPeople()} disabled={clustering}>
          {clustering ? <span className="spinner spinner-inline" /> : null}
          {clustering ? 'Grouping…' : 'Find people'}
        </button>
      </div>
      <div className="people-grid">
        {persons.map((p) => (
          <a key={p.id} className="person-card" href={`#/people/${p.id}`}>
            <PersonAvatar person={p} />
            <strong>{p.name}</strong>
            <span>{p.face_count} face{p.face_count === 1 ? '' : 's'}</span>
          </a>
        ))}
      </div>

      {unclustered.length > 0 && (
        <section className="unclustered-strip">
          <div className="unclustered-head">
            <span>
              {unclustered.length} face{unclustered.length === 1 ? '' : 's'} not grouped yet —
              click one to say who it is, or run “Find people”.
            </span>
          </div>
          <div className="face-row">
            {unclustered.slice(0, 40).map((f) => (
              <UnassignedFace
                key={f.id}
                face={f}
                persons={persons}
                onAssign={(pid) => void assignFace(f.id, pid)}
              />
            ))}
          </div>
        </section>
      )}

      {hiddenPersons.length > 0 && (
        <section className="hidden-people">
          <h2 className="section-title">Hidden ({hiddenPersons.length})</h2>
          <div className="people-grid">
            {hiddenPersons.map((p) => (
              <div key={p.id} className="person-card person-card-hidden">
                <PersonAvatar person={p} />
                <strong>{p.name}</strong>
                <button className="btn-chip" onClick={() => void unhide(p)}>Show again</button>
              </div>
            ))}
          </div>
        </section>
      )}

      {persons.length === 0 && unclustered.length === 0 && !clustering && (
        <div className="empty-state">
          <h2>Nobody recognized yet</h2>
          <p className="empty-hint">Run “Find people” after a scan to group faces — everything runs locally.</p>
        </div>
      )}
    </div>
  );
}

function UnassignedFace({
  face,
  persons,
  onAssign,
}: {
  face: Face;
  persons: Person[];
  onAssign: (personId: number | null) => void;
}) {
  const url = useImage(face.thumbnail, 128, true);
  const [picking, setPicking] = useState(false);
  return (
    <div className="face-cell">
      <button
        className="face-thumb"
        title="Assign this face"
        onClick={() => (persons.length ? setPicking((v) => !v) : onAssign(null))}
      >
        {url ? <img src={url} alt="" /> : <div className="thumb-loading" />}
      </button>
      {picking && (
        <div className="person-picker">
          <div className="person-picker-title">Who is this?</div>
          <div className="person-picker-list">
            {persons.map((p) => (
              <button key={p.id} onClick={() => { setPicking(false); onAssign(p.id); }}>
                {p.name}
              </button>
            ))}
          </div>
          <button className="btn-chip" onClick={() => { setPicking(false); onAssign(null); }}>
            Leave ungrouped
          </button>
        </div>
      )}
    </div>
  );
}

function PersonAvatar({ person }: { person: Person }) {
  const url = useImage(person.thumbnail, 384, true);
  return (
    <div className="person-avatar">
      {url ? <img src={url} alt="" /> : <div className="thumb-loading" />}
    </div>
  );
}

interface PersonFace {
  id: number;
  content_hash: string;
  thumbnail: string | null;
}

function PersonDetail({ personId }: { personId: number }) {
  const libraryPath = useStore((s) => s.libraryPath);
  const openViewer = useStore((s) => s.openViewer);
  const refreshToken = useStore((s) => s.refreshToken);
  const navigate = useStore((s) => s.navigate);
  const showToast = useStore((s) => s.showToast);
  const refresh = useStore((s) => s.refresh);
  const [person, setPerson] = useState<Person | null>(null);
  const [photos, setPhotos] = useState<Item[]>([]);
  const [faces, setFaces] = useState<PersonFace[]>([]);
  const [splitPicks, setSplitPicks] = useState<Set<number>>(new Set());
  const [merging, setMerging] = useState(false);

  const reload = useCallback(() => {
    if (!libraryPath) return;
    void backend
      .getPersons(libraryPath)
      .then((res) => setPerson((res.persons as Person[]).find((p) => p.id === personId) ?? null));
    void backend.getPhotosByPerson(libraryPath, personId).then((res) => {
      const rows = (res.photos as { path: string; content_hash: string; kind?: string }[]) ?? [];
      setPhotos(
        rows.map((p) => ({
          path: p.path,
          content_hash: p.content_hash,
          kind: p.kind ?? 'photo',
          ts: 0,
        })) as Item[]
      );
    });
    void backend.getPersonFaces(libraryPath, personId).then((res) => setFaces((res.faces as PersonFace[]) ?? []));
  }, [libraryPath, personId]);

  useEffect(() => {
    reload();
  }, [reload, refreshToken]);

  if (!person) return <div className="page"><span className="spinner" /></div>;

  const mergeInto = async (other: Person) => {
    if (!libraryPath) return;
    if (!window.confirm(`Merge “${person.name}” into “${other.name}”? All faces move to “${other.name}”.`)) return;
    await backend.mergePersons(libraryPath, other.id, person.id);
    showToast({ text: `Merged into “${other.name}”`, kind: 'info' });
    setMerging(false);
    refresh();
    navigate({ page: 'people', personId: other.id });
  };

  const hidePerson = async () => {
    if (!libraryPath) return;
    if (!window.confirm(`Hide “${person.name}”? They disappear from People until you show them again.`)) return;
    await backend.setPersonHidden(libraryPath, person.id, true);
    showToast({ text: 'Person hidden', kind: 'info' });
    refresh();
    navigate({ page: 'people' });
  };

  const setFeaturePhoto = async (face: PersonFace) => {
    if (!libraryPath || !face.thumbnail) return;
    await backend.setPersonThumbnail(libraryPath, person.id, face.thumbnail);
    showToast({ text: 'Feature photo updated', kind: 'info' });
    reload();
  };

  const splitOff = async () => {
    if (!libraryPath || splitPicks.size === 0) return;
    const name = await promptText({
      title: `Name for the new person (${splitPicks.size} face${splitPicks.size === 1 ? '' : 's'} split off) — leave empty to name later:`,
    });
    if (name === null) return;
    try {
      await backend.splitPerson(libraryPath, person.id, [...splitPicks], name || undefined);
      showToast({ text: name ? `Split off “${name}”` : 'Faces split into a new person', kind: 'info' });
      setSplitPicks(new Set());
      refresh();
      reload();
    } catch (e) {
      showToast({ text: e instanceof Error ? e.message : 'Split failed', kind: 'error' });
    }
  };

  return (
    <div className="page">
      <a className="crumb" href="#/people">← People</a>
      <div className="person-head">
        <div className="person-avatar person-avatar-big"><PersonAvatar person={person} /></div>
        <div>
          <EditableName person={person} onRenamed={(name) => setPerson({ ...person, name })} />
          <div className="info-muted">{person.face_count} faces</div>
          <div className="btn-row">
            <button className="btn-chip" onClick={() => setMerging((v) => !v)}>Merge into…</button>
            <button className="btn-chip" onClick={() => void hidePerson()}>Hide person</button>
          </div>
        </div>
      </div>

      {merging && (
        <PersonPicker
          title={`Merge “${person.name}” into which person?`}
          excludeId={person.id}
          libraryPath={libraryPath ?? ''}
          onPick={(other) => void mergeInto(other)}
          onCancel={() => setMerging(false)}
        />
      )}

      {faces.length > 1 && (
        <section className="faces-tools">
          <div className="unclustered-head">
            <span>
              Click a face to mark it — “Feature photo” sets the portrait; marked faces can be
              split into a new person.
            </span>
            {splitPicks.size > 0 && (
              <button className="btn btn-small" onClick={() => void splitOff()}>
                Split {splitPicks.size} into new person…
              </button>
            )}
          </div>
          <div className="face-row">
            {faces.map((f) => {
              const picked = splitPicks.has(f.id);
              return (
                <div key={f.id} className={`face-cell ${picked ? 'face-cell-picked' : ''}`}>
                  <button
                    className="face-thumb"
                    title="Feature photo (double-click to toggle split pick)"
                    onClick={() => void setFeaturePhoto(f)}
                    onDoubleClick={() =>
                      setSplitPicks((prev) => {
                        const next = new Set(prev);
                        if (next.has(f.id)) next.delete(f.id);
                        else next.add(f.id);
                        return next;
                      })
                    }
                  >
                    <FaceThumb hash={f.thumbnail} />
                    {picked && <span className="thumb-check" aria-hidden>✓</span>}
                  </button>
                  <button
                    className="face-split-toggle"
                    title="Mark for split"
                    onClick={() =>
                      setSplitPicks((prev) => {
                        const next = new Set(prev);
                        if (next.has(f.id)) next.delete(f.id);
                        else next.add(f.id);
                        return next;
                      })
                    }
                  >
                    {picked ? 'Unmark' : 'Mark'}
                  </button>
                </div>
              );
            })}
          </div>
        </section>
      )}

      {photos.length > 0 ? (
        <PhotoGrid
          groups={[{ key: 'all', items: photos }]}
          view="days"
          onOpen={openViewer}
          flatten
        />
      ) : (
        <div className="empty-state"><h2>No photos</h2></div>
      )}
    </div>
  );
}

function FaceThumb({ hash }: { hash: string | null }) {
  const url = useImage(hash, 128, true);
  return url ? <img src={url} alt="" /> : <div className="thumb-loading" />;
}

function PersonPicker({
  title,
  excludeId,
  libraryPath,
  onPick,
  onCancel,
}: {
  title: string;
  excludeId: number;
  libraryPath: string;
  onPick: (person: Person) => void;
  onCancel: () => void;
}) {
  const [persons, setPersons] = useState<Person[] | null>(null);
  useEffect(() => {
    if (!libraryPath) return;
    void backend.getPersons(libraryPath).then((res) => setPersons((res.persons as Person[]) ?? []));
  }, [libraryPath]);
  return (
    <div className="person-picker person-picker-modal">
      <div className="person-picker-title">{title}</div>
      {persons === null ? (
        <span className="spinner spinner-inline" />
      ) : (
        <div className="person-picker-list person-picker-grid">
          {persons
            .filter((p) => p.id !== excludeId)
            .map((p) => (
              <button key={p.id} className="person-picker-option" onClick={() => onPick(p)}>
                <PersonAvatar person={p} />
                <span>{p.name}</span>
              </button>
            ))}
        </div>
      )}
      <button className="btn-chip" onClick={onCancel}>Cancel</button>
    </div>
  );
}

function EditableName({ person, onRenamed }: { person: Person; onRenamed: (name: string) => void }) {
  const libraryPath = useStore((s) => s.libraryPath);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(person.name);

  if (!editing) {
    return (
      <button className="person-name" onClick={() => setEditing(true)} title="Rename">
        {person.name} <span className="edit-pencil">✎</span>
      </button>
    );
  }
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        if (libraryPath && name.trim()) {
          await backend.renamePerson(libraryPath, person.id, name.trim());
          onRenamed(name.trim());
        }
        setEditing(false);
      }}
    >
      <input
        className="person-name-input"
        autoFocus
        value={name}
        onChange={(e) => setName(e.target.value)}
        onBlur={() => setEditing(false)}
      />
    </form>
  );
}
