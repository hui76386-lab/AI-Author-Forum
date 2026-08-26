const API_ROOT = '/reader-api/v1';

export function element(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
}

export function csrfToken() {
    const item = document.cookie
        .split(';')
        .map((part) => part.trim())
        .find((part) => part.startsWith('csrftoken='));
    return item ? decodeURIComponent(item.slice('csrftoken='.length)) : '';
}

export function operationId() {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
    if (window.crypto?.getRandomValues) {
        const bytes = new Uint8Array(16);
        window.crypto.getRandomValues(bytes);
        return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('');
    }
    return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export async function request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set('Accept', 'application/json');
    if (options.body) {
        headers.set('Content-Type', 'application/json');
        headers.set('X-CSRFToken', csrfToken());
    }
    const response = await fetch(`${API_ROOT}${path}`, {
        credentials: 'same-origin',
        ...options,
        headers,
    });
    if (response.status === 304) return { notModified: true, response };
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        const error = new Error(payload.error?.message || 'Request failed.');
        error.code = payload.error?.code || 'service_degraded';
        error.retryAfter = response.headers.get('Retry-After');
        throw error;
    }
    return { data: payload.data, response };
}
