(() => {
  const form = document.querySelector('[data-reader-verification-form]');
  const tokenField = document.querySelector('[data-reader-verification-token]');
  const status = document.querySelector('[data-reader-verification-status]');
  if (!form || !tokenField || !status) return;
  const submitButton = form.querySelector('[data-reader-verification-submit]');

  const fragment = new URLSearchParams(window.location.hash.slice(1));
  tokenField.value = fragment.get('token') || '';
  window.history.replaceState(
    {},
    document.title,
    window.location.pathname + window.location.search,
  );
  if (!tokenField.value) {
    status.textContent = 'This link must be opened from the email.';
    status.hidden = false;
    if (submitButton) submitButton.hidden = false;
  }

  let submitting = false;
  const consume = async () => {
    if (!tokenField.value || submitting) return;
    submitting = true;
    if (submitButton) submitButton.disabled = true;
    status.textContent = 'Verifying your email and unlocking the requesting computer...';
    status.hidden = false;
    try {
      const csrfToken = form.querySelector('[name="csrfmiddlewaretoken"]')?.value || '';
      const response = await fetch(form.action, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken,
        },
        body: JSON.stringify({
          token: tokenField.value,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.error?.code || 'verification_failed');
      const returnTo = payload?.data?.return_to || '/';
      const target = new URL(returnTo, window.location.origin);
      if (target.origin !== window.location.origin) throw new Error('invalid_return_path');
      if (!target.hash) target.hash = 'reader-interactions';
      window.location.assign(`${target.pathname}${target.search}${target.hash}`);
    } catch (_error) {
      status.textContent = 'Reader access could not be confirmed. Request a new link.';
      status.hidden = false;
      submitting = false;
      if (submitButton) {
        submitButton.disabled = false;
        submitButton.hidden = false;
      }
    }
  };

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    void consume();
  });
  if (tokenField.value) void consume();
})();
