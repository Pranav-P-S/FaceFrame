import { useCallback, useEffect, useState } from 'react';
import { api, type Item, type Person } from '../../types';
import { backend } from '../../lib/api';
import { useStore } from '../../lib/store';
import PhotoGrid from '../../components/PhotoGrid';
import { useImage } from '../../images';

/** People grid or one person's photos depending on the route. */
export default function PeoplePage() {
  const route = useStore((s) => s.route);
  const libraryPath = useStore((s) => s.libraryPath);
  const refreshToken = useStore((s) => s.refreshToken);
  const [persons, setPersons] = useState<Person[]>([]);
  const [unclustered, setUnclustered] = useState(0);
  const [clustering, setClustering] = useState(false);
  const showToast = useStore((s) => s.showToast);

  const refresh = useCallback(() => {
    if (!libraryPath) return;
    void backend.getPersons(libraryPath).then((res) => setPersons((res.persons as Person[]) ?? []));
    void backend.getUnclustered(libraryPath).then((res) => setUnclustered(((res.faces as unknown[]) ?? []).length));
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
      {unclustered > 0 && (
        <div className="notice notice-info">
          {unclustered} face{unclustered === 1 ? '' : 's'} not grouped yet —
          <button className="btn btn-small" onClick={() => void findPeople()}>Find people</button>
        </div>
      )}
      {persons.length === 0 && !clustering && (
        <div className="empty-state">
          <h2>Nobody recognized yet</h2>
          <p className="empty-hint">Run “Find people” after a scan to group faces — everything runs locally.</p>
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

function PersonDetail({ personId }: { personId: number }) {
  const libraryPath = useStore((s) => s.libraryPath);
  const openViewer = useStore((s) => s.openViewer);
  const refreshToken = useStore((s) => s.refreshToken);
  const [person, setPerson] = useState<Person | null>(null);
  const [photos, setPhotos] = useState<Item[]>([]);

  useEffect(() => {
    if (!libraryPath) return;
    void backend
      .getPersons(libraryPath)
      .then((res) => setPerson((res.persons as Person[]).find((p) => p.id === personId) ?? null));
    void backend.getPhotosByPerson(libraryPath, personId).then((res) => {
      const paths = (res.photos as { path: string; face_count: number }[]) ?? [];
      setPhotos(
        paths.map((p) => ({
          path: p.path,
          content_hash: p.path,
          kind: 'photo',
          ts: 0,
        })) as Item[]
      );
    });
  }, [libraryPath, personId, refreshToken]);

  if (!person) return <div className="page"><span className="spinner" /></div>;

  return (
    <div className="page">
      <a className="crumb" href="#/people">← People</a>
      <div className="person-head">
        <div className="person-avatar person-avatar-big"><PersonAvatar person={person} /></div>
        <div>
          <EditableName person={person} onRenamed={(name) => setPerson({ ...person, name })} />
          <div className="info-muted">{person.face_count} faces</div>
        </div>
      </div>
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
