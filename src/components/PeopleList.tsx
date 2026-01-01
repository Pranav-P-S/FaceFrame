import { useState, useRef, useEffect } from 'react';

interface Person {
    id: number;
    name: string;
    count: number;
    thumbnail?: string;
}

interface PeopleListProps {
    persons: Person[];
    folderPath: string;
    onPersonClick?: (person: Person) => void;
    onRefresh?: () => void;
}

// Helper to convert Windows paths to safe-file:// URLs
function toSafeFileUrl(path: string | undefined): string | null {
    if (!path) return null;
    // Replace backslashes with forward slashes
    let normalized = path.replace(/\\/g, '/');
    return `safe-file://${normalized}`;
}

export default function PeopleList({ persons, folderPath, onPersonClick, onRefresh }: PeopleListProps) {
    const [selectedForMerge, setSelectedForMerge] = useState<number | null>(null);
    const [editingId, setEditingId] = useState<number | null>(null);
    const [editName, setEditName] = useState<string>('');
    const inputRef = useRef<HTMLInputElement>(null);

    // Focus input when editing starts
    useEffect(() => {
        if (editingId !== null && inputRef.current) {
            inputRef.current.focus();
            inputRef.current.select();
        }
    }, [editingId]);

    if (persons.length === 0) {
        return <div style={{ padding: '20px', color: '#888' }}>No people identified yet. Scan a folder and click "Find People".</div>;
    }

    const handleRename = async (personId: number) => {
        if (editName.trim()) {
            await window.electronAPI.renamePerson(folderPath, personId, editName.trim());
            setEditingId(null);
            setEditName('');
            onRefresh?.();
        }
    };

    const handleMerge = async (keepId: number, mergeId: number) => {
        if (confirm(`Merge these two people? All faces from the second person will be moved to the first.`)) {
            await window.electronAPI.mergePersons(folderPath, keepId, mergeId);
            setSelectedForMerge(null);
            onRefresh?.();
        }
    };

    const startEdit = (person: Person) => {
        setEditingId(person.id);
        setEditName(person.name);
    };

    return (
        <div style={{ width: '100%', padding: '20px' }}>
            <h3 style={{ borderBottom: '1px solid #444', paddingBottom: '10px', marginBottom: '15px' }}>
                People ({persons.length})
                {selectedForMerge && (
                    <span style={{ fontSize: '12px', color: '#f50', marginLeft: '10px' }}>
                        Select another person to merge with
                        <button onClick={() => setSelectedForMerge(null)} style={{ marginLeft: '10px', cursor: 'pointer' }}>Cancel</button>
                    </span>
                )}
            </h3>
            <div style={{
                display: 'flex',
                overflowX: 'auto',
                gap: '15px',
                padding: '10px 0',
                whiteSpace: 'nowrap'
            }}>
                {persons.map(p => {
                    const thumbUrl = toSafeFileUrl(p.thumbnail);

                    return (
                        <div
                            key={p.id}
                            onClick={() => {
                                if (editingId) return; // Don't process clicks while editing
                                if (selectedForMerge && selectedForMerge !== p.id) {
                                    handleMerge(selectedForMerge, p.id);
                                } else {
                                    onPersonClick?.(p);
                                }
                            }}
                            style={{
                                flex: '0 0 auto',
                                width: '120px',
                                textAlign: 'center',
                                background: selectedForMerge === p.id ? '#5a4' : '#333',
                                padding: '10px',
                                borderRadius: '8px',
                                cursor: editingId ? 'default' : 'pointer',
                                border: selectedForMerge && selectedForMerge !== p.id ? '2px dashed #f50' : '2px solid transparent'
                            }}>
                            <div style={{
                                width: '80px',
                                height: '80px',
                                borderRadius: '50%',
                                background: '#555',
                                margin: '0 auto 10px',
                                overflow: 'hidden',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                fontSize: '24px'
                            }}>
                                {thumbUrl ? (
                                    <img
                                        src={thumbUrl}
                                        style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                                        onError={(e) => {
                                            console.error('Failed to load thumbnail:', thumbUrl);
                                            (e.target as HTMLImageElement).style.display = 'none';
                                        }}
                                    />
                                ) : (
                                    <span>{p.name?.[0] || '?'}</span>
                                )}
                            </div>

                            {editingId === p.id ? (
                                <div
                                    onClick={(e) => e.stopPropagation()}
                                    onMouseDown={(e) => e.stopPropagation()}
                                >
                                    <input
                                        ref={inputRef}
                                        type="text"
                                        value={editName}
                                        onChange={(e) => setEditName(e.target.value)}
                                        onKeyDown={(e) => {
                                            e.stopPropagation();
                                            if (e.key === 'Enter') handleRename(p.id);
                                            if (e.key === 'Escape') setEditingId(null);
                                        }}
                                        onBlur={() => { }} // Keep open on blur
                                        style={{
                                            width: '100%',
                                            padding: '4px',
                                            borderRadius: '4px',
                                            border: '1px solid #666',
                                            background: '#222',
                                            color: 'white',
                                            outline: 'none'
                                        }}
                                    />
                                    <div style={{ marginTop: '5px', display: 'flex', gap: '5px', justifyContent: 'center' }}>
                                        <button
                                            onClick={(e) => { e.stopPropagation(); handleRename(p.id); }}
                                            style={{ fontSize: '10px', padding: '2px 6px' }}
                                        >
                                            Save
                                        </button>
                                        <button
                                            onClick={(e) => { e.stopPropagation(); setEditingId(null); }}
                                            style={{ fontSize: '10px', padding: '2px 6px' }}
                                        >
                                            Cancel
                                        </button>
                                    </div>
                                </div>
                            ) : (
                                <>
                                    <div style={{ fontWeight: 'bold', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.name}</div>
                                    <div style={{ fontSize: '12px', color: '#888', marginBottom: '8px' }}>{p.count || 0} photos</div>
                                    <div style={{ display: 'flex', gap: '5px', justifyContent: 'center' }} onClick={(e) => e.stopPropagation()}>
                                        <button
                                            onClick={(e) => { e.stopPropagation(); startEdit(p); }}
                                            style={{ fontSize: '10px', padding: '2px 6px', cursor: 'pointer' }}
                                        >
                                            Rename
                                        </button>
                                        <button
                                            onClick={(e) => { e.stopPropagation(); setSelectedForMerge(p.id); }}
                                            style={{ fontSize: '10px', padding: '2px 6px', cursor: 'pointer' }}
                                        >
                                            Merge
                                        </button>
                                    </div>
                                </>
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
}
