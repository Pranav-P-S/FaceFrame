/** TypeScript mirror of python-backend/query.py — keep the two in sync.
 * Used to render filter chips client-side without a round trip. */

export interface ParsedQuery {
  text: string[];
  filters: Record<string, string[]>;
}

const KNOWN_KEYS = new Set([
  'person', 'people', 'place', 'type', 'is', 'folder', 'in',
  'after', 'before', 'on', 'year', 'label',
]);

/** Minimal shlex-like tokenizer supporting double quotes. */
function tokenize(raw: string): string[] {
  const tokens: string[] = [];
  let current = '';
  let inQuotes = false;
  for (const ch of raw) {
    if (ch === '"') {
      inQuotes = !inQuotes;
    } else if (ch === ' ' && !inQuotes) {
      if (current) tokens.push(current);
      current = '';
    } else {
      current += ch;
    }
  }
  if (current) tokens.push(current);
  return tokens;
}

export function parse(raw: string): ParsedQuery {
  const text: string[] = [];
  const filters: Record<string, string[]> = {};
  for (const token of tokenize(raw || '')) {
    const idx = token.indexOf(':');
    if (idx > 0) {
      const key = token.slice(0, idx).toLowerCase();
      const value = token.slice(idx + 1);
      if (key === 'year' && /^\d{4}$/.test(value)) {
        (filters.after ??= []).push(`${value}-01-01`);
        (filters.before ??= []).push(`${Number(value) + 1}-01-01`);
        continue;
      }
      if (KNOWN_KEYS.has(key) && value) {
        const normalized = key === 'people' ? 'person' : key === 'in' ? 'place' : key;
        (filters[normalized] ??= []).push(value);
        continue;
      }
    }
    text.push(token);
  }
  return { text, filters };
}
