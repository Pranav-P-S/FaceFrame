import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../types';
import { backend } from '../lib/api';
import { useStore } from '../lib/store';

export interface EditState {
  crop?: [number, number, number, number]; // normalized x, y, w, h
  rotate?: 0 | 90 | 180 | 270;
  flip_h?: boolean;
  flip_v?: boolean;
  straighten?: number;
  adjust?: Record<string, number>;
  filter?: string;
  eraser?: number[][];
}

const FILTERS = [
  'none', 'auto', 'vivid', 'natural', 'warm', 'cool', 'golden',
  'mono', 'mono_high', 'sepia', 'fade', 'cinema', 'dreamy', 'noir',
];

const ADJUSTMENTS: { key: string; label: string; min: number; max: number; def: number; step: number }[] = [
  { key: 'brightness', label: 'Brightness', min: 0.5, max: 1.5, def: 1, step: 0.01 },
  { key: 'contrast', label: 'Contrast', min: 0.5, max: 1.5, def: 1, step: 0.01 },
  { key: 'saturation', label: 'Saturation', min: 0, max: 2, def: 1, step: 0.01 },
  { key: 'warmth', label: 'Warmth', min: -0.5, max: 0.5, def: 0, step: 0.01 },
  { key: 'highlights', label: 'Highlights', min: -0.5, max: 0.5, def: 0, step: 0.01 },
  { key: 'shadows', label: 'Shadows', min: -0.5, max: 0.5, def: 0, step: 0.01 },
  { key: 'sharpen', label: 'Sharpen', min: -1, max: 1.5, def: 0, step: 0.05 },
  { key: 'vignette', label: 'Vignette', min: 0, max: 0.8, def: 0, step: 0.01 },
];

/** Non-destructive editor: manipulates an edit document; every preview is
 * rendered by the backend from (content, edit). Originals stay untouched. */
export default function Editor({
  filePath,
  hash,
  initial,
  width,
  height,
  onClose,
}: {
  filePath: string;
  hash: string;
  initial: EditState | null;
  width?: number | null;
  height?: number | null;
  onClose: () => void;
}) {
  const showToast = useStore((s) => s.showToast);
  const [tab, setTab] = useState<'adjust' | 'crop' | 'filters' | 'eraser'>('adjust');
  const [edit, setEditState] = useState<EditState>(initial ?? {});
  const [preview, setPreview] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const debounceRef = useRef<number | null>(null);
  const [rects, setRects] = useState<number[][]>(initial?.eraser ?? []);
  const dragStart = useRef<[number, number] | null>(null);
  const [currentRect, setCurrentRect] = useState<number[] | null>(null);
  const previewBoxRef = useRef<HTMLDivElement>(null);

  const update = useCallback((patch: Partial<EditState>) => {
    setEditState((prev) => ({ ...prev, ...patch }));
  }, []);

  // Debounced backend-rendered preview with the candidate edit.
  useEffect(() => {
    if (debounceRef.current !== null) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(async () => {
      setBusy(true);
      try {
        const hasEdit = Object.keys(edit).length > 0 || rects.length > 0;
        const res = await api().request('get_image_preview', {
          file_path: filePath,
          max_dim: 1600,
          // An empty local edit must preview the ORIGINAL, not the stored one
          ...(hasEdit ? { edit } : { apply_edit: false }),
        });
        setPreview((res as { data_url?: string }).data_url ?? null);
      } finally {
        setBusy(false);
      }
    }, 180);
    return () => {
      if (debounceRef.current !== null) window.clearTimeout(debounceRef.current);
    };
  }, [edit, filePath]);

  const save = async () => {
    const hasEdit = Object.keys(edit).length > 0;
    await backend.setEdit(hash, hasEdit ? { ...edit, eraser: rects.length ? rects : undefined } : null);
    showToast({ text: hasEdit ? 'Edits saved — original kept' : 'Edits reverted', kind: 'info' });
    onClose();
  };

  const setAdjust = (key: string, value: number) => {
    const adjust = { ...(edit.adjust ?? {}) };
    if (value === ADJUSTMENTS.find((a) => a.key === key)?.def) delete adjust[key];
    else adjust[key] = value;
    update({ adjust: Object.keys(adjust).length ? adjust : undefined });
  };

  // Crop is previewed via CSS transforms on the un-cropped render; the
  // normalized rect is committed to the edit document live.
  const onEraserPointer = (e: React.PointerEvent, isStart: boolean) => {
    const box = previewBoxRef.current;
    if (!box) return;
    const rect = box.getBoundingClientRect();
    const x = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const y = Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height));
    if (isStart) {
      dragStart.current = [x, y];
      setCurrentRect([x, y, 0, 0]);
    } else if (dragStart.current) {
      const [x0, y0] = dragStart.current;
      setCurrentRect([Math.min(x0, x), Math.min(y0, y), Math.abs(x - x0), Math.abs(y - y0)]);
    }
  };

  const commitRect = () => {
    if (currentRect && currentRect[2] > 0.01 && currentRect[3] > 0.01) {
      setRects((r) => [...r, currentRect]);
      update({ eraser: [...rects, currentRect] });
    }
    setCurrentRect(null);
    dragStart.current = null;
  };

  return (
    <div className="editor" role="dialog" aria-label="Photo editor">
      <div className="editor-topbar">
        <button className="btn-ghost" onClick={onClose}>← Back</button>
        <span className="editor-title">Edit photo</span>
        <div className="topbar-spacer" />
        <button className="btn-ghost" onClick={async () => { setEditState({}); setRects([]); }}>Revert</button>
        <button className="btn" onClick={save}>{busy ? '…' : 'Save'}</button>
      </div>

      <div className="editor-canvas">
        {preview ? (
          <div
            className={`editor-preview ${tab === 'eraser' ? 'editor-eraser-mode' : ''}`}
            ref={previewBoxRef}
            onPointerDown={(e) => tab === 'eraser' && onEraserPointer(e, true)}
            onPointerMove={(e) => tab === 'eraser' && dragStart.current && onEraserPointer(e, false)}
            onPointerUp={() => tab === 'eraser' && commitRect()}
          >
            {preview ? <img src={preview} alt="" /> : null}
            {currentRect && (
              <div
                className="eraser-rect"
                style={{
                  left: `${currentRect[0] * 100}%`,
                  top: `${currentRect[1] * 100}%`,
                  width: `${currentRect[2] * 100}%`,
                  height: `${currentRect[3] * 100}%`,
                }}
              />
            )}
            {rects.map((r, i) => (
              <div
                key={i}
                className="eraser-rect eraser-rect-done"
                style={{ left: `${r[0] * 100}%`, top: `${r[1] * 100}%`, width: `${r[2] * 100}%`, height: `${r[3] * 100}%` }}
              />
            ))}
          </div>
        ) : (
          <div className="editor-loading"><span className="spinner" /></div>
        )}
      </div>

      <div className="editor-tabs">
        {(['adjust', 'filters', 'crop', 'eraser'] as const).map((t) => (
          <button key={t} className={`editor-tab ${tab === t ? 'editor-tab-active' : ''}`} onClick={() => setTab(t)}>
            {t === 'adjust' ? 'Adjust' : t === 'filters' ? 'Filters' : t === 'crop' ? 'Crop & rotate' : 'Magic eraser'}
          </button>
        ))}
      </div>

      <div className="editor-panel">
        {tab === 'adjust' && (
          <div className="editor-sliders">
            {ADJUSTMENTS.map((a) => (
              <label key={a.key} className="editor-slider">
                <span>{a.label}</span>
                <input
                  type="range"
                  min={a.min}
                  max={a.max}
                  step={a.step}
                  value={edit.adjust?.[a.key] ?? a.def}
                  onChange={(e) => setAdjust(a.key, Number(e.target.value))}
                />
              </label>
            ))}
          </div>
        )}
        {tab === 'filters' && (
          <div className="editor-filterlist">
            {FILTERS.map((f) => (
              <button
                key={f}
                className={`filter-chip ${edit.filter === f || (f === 'none' && !edit.filter) ? 'filter-chip-active' : ''}`}
                onClick={() => update({ filter: f === 'none' ? undefined : f })}
              >
                {f.replace('_', ' ')}
              </button>
            ))}
          </div>
        )}
        {tab === 'crop' && (
          <div className="editor-cropcontrols">
            <div className="editor-slider">
              <span>Straighten</span>
              <input
                type="range"
                min={-15}
                max={15}
                step={0.5}
                value={edit.straighten ?? 0}
                onChange={(e) => update({ straighten: Number(e.target.value) || undefined })}
              />
            </div>
            <div className="editor-btnrow">
              {[0, 90, 180, 270].map((deg) => (
                <button key={deg} className={`btn-chip ${(edit.rotate ?? 0) === deg ? 'btn-chip-active' : ''}`} onClick={() => update({ rotate: deg as 0 | 90 | 180 | 270 })}>
                  {deg}°
                </button>
              ))}
              <button className={`btn-chip ${edit.flip_h ? 'btn-chip-active' : ''}`} onClick={() => update({ flip_h: !edit.flip_h })}>Flip H</button>
              <button className={`btn-chip ${edit.flip_v ? 'btn-chip-active' : ''}`} onClick={() => update({ flip_v: !edit.flip_v })}>Flip V</button>
            </div>
            <div className="editor-btnrow">
              <span className="editor-hint-inline">Aspect:</span>
              {(['free', '1:1', '4:3', '16:9'] as const).map((preset) => (
                <button
                  key={preset}
                  className="btn-chip"
                  onClick={() => {
                    if (preset === 'free' || !width || !height) {
                      update({ crop: undefined });
                      return;
                    }
                    const [rw, rh] = preset === '1:1' ? [1, 1] : preset === '4:3' ? [4, 3] : [16, 9];
                    const srcAspect = width / height;
                    let cw = 1;
                    let ch = 1;
                    if (srcAspect > rw / rh) cw = (rw / rh) / srcAspect;
                    else ch = srcAspect / (rw / rh);
                    update({ crop: [(1 - cw) / 2, (1 - ch) / 2, cw, ch] });
                  }}
                >
                  {preset}
                </button>
              ))}
              {edit.crop && <span className="editor-hint-inline">centered {edit.crop[2].toFixed(2)}×</span>}
            </div>
          </div>
        )}
        {tab === 'eraser' && (
          <div className="editor-cropcontrols">
            <div className="editor-hint">
              Drag a rectangle over an object to remove it. Erasures are applied
              when you save. {rects.length} area(s) marked.
            </div>
            {rects.length > 0 && (
              <button className="btn-chip" onClick={() => { setRects([]); update({ eraser: undefined }); }}>
                Clear all areas
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
