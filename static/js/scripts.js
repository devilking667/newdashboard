// Confirm start/stop bot actions
document.addEventListener('DOMContentLoaded', () => {
  const forms = document.querySelectorAll('form[data-confirm]');
  forms.forEach(form => {
    form.addEventListener('submit', (e) => {
      const msg = form.getAttribute('data-confirm');
      if (!confirm(msg)) {
        e.preventDefault();
      }
    });
  });

  // Disable submit buttons after submit to prevent double submit
  const submitForms = document.querySelectorAll('form');
  submitForms.forEach(form => {
    form.addEventListener('submit', () => {
      const btn = form.querySelector('button[type="submit"]');
      if (btn) btn.disabled = true;
    });
  });
});
