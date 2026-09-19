/** In-app replacement for window.prompt, which Electron does not implement
 * (it throws). Renders a small modal over the page and resolves with the
 * entered text, or null when cancelled. window.confirm IS supported and
 * stays in use for yes/no questions. */

interface PromptOptions {
  title: string;
  placeholder?: string;
  initial?: string;
  okLabel?: string;
}

export function promptText({ title, placeholder = '', initial = '', okLabel = 'OK' }: PromptOptions): Promise<string | null> {
  return new Promise((resolve) => {
    const backdrop = document.createElement('div');
    backdrop.className = 'ff-prompt-backdrop';

    const finish = (value: string | null) => {
      window.removeEventListener('keydown', onKey, true);
      backdrop.remove();
      resolve(value);
    };

    const form = document.createElement('form');
    form.className = 'ff-prompt';

    const label = document.createElement('div');
    label.className = 'ff-prompt-title';
    label.textContent = title;

    const input = document.createElement('input');
    input.className = 'ff-prompt-input';
    input.placeholder = placeholder;
    input.value = initial;
    input.autofocus = true;

    const row = document.createElement('div');
    row.className = 'ff-prompt-actions';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'btn-ghost';
    cancel.textContent = 'Cancel';
    cancel.addEventListener('click', () => finish(null));
    const ok = document.createElement('button');
    ok.type = 'submit';
    ok.className = 'btn';
    ok.textContent = okLabel;
    row.append(cancel, ok);

    form.append(label, input, row);
    backdrop.append(form);
    backdrop.addEventListener('mousedown', (e) => {
      if (e.target === backdrop) finish(null);
    });
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const value = input.value.trim();
      finish(value || null);
    });

    const onKey = (e: KeyboardEvent) => {
      // Escape closes the prompt and must not reach page-level handlers
      // (viewer, palette) underneath.
      if (e.key === 'Escape') {
        e.stopPropagation();
        finish(null);
      }
    };
    window.addEventListener('keydown', onKey, true);

    document.body.append(backdrop);
    input.focus();
    input.select();
  });
}
