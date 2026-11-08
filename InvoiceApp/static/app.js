// Spreadsheet keys for the ledger: Enter saves the cell (and moves down), Esc puts it back the way it was.
document.addEventListener('keydown', (e) => {
  const el = e.target;
  if (!el.matches || !el.matches('.sheet .c')) return;
  if (e.key === 'Escape') {
    if ('defaultValue' in el && el.tagName === 'INPUT') el.value = el.defaultValue;
    el.blur();
  } else if (e.key === 'Enter' && el.tagName === 'INPUT') {
    e.preventDefault();
    const td = el.closest('td'), tr = el.closest('tr');
    const next = tr.nextElementSibling && tr.nextElementSibling.children[td.cellIndex];
    const target = next && next.querySelector('.c');
    el.blur();                       // fires "change", which saves
    if (target) target.focus();
  }
});

// After a cell saves, remember the saved value so Esc returns to it (not to what the page first loaded).
document.addEventListener('htmx:afterRequest', (e) => {
  const el = e.detail.elt;
  if (el && el.matches && el.matches('.sheet input.c') && e.detail.successful) el.defaultValue = el.value;
});

// Number boxes shouldn't change when you scroll past them with a mouse wheel or trackpad.
document.addEventListener('wheel', (e) => {
  if (e.target.matches && e.target.matches('input[type=number]') && document.activeElement === e.target) e.target.blur();
}, { passive: true });

// Toast fades out by itself.
let toastTimer;
document.addEventListener('htmx:oobAfterSwap', (e) => {
  if (e.detail.target && e.detail.target.id === 'toast') {
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => e.detail.target.classList.remove('show'), 3500);
  }
});

// Pop-out forms close when you tap elsewhere.
document.addEventListener('click', (e) => {
  document.querySelectorAll('details.pop[open]').forEach((d) => { if (!d.contains(e.target)) d.removeAttribute('open'); });
});
