const INTENT_PREFIX = 'reader-interactions:pending-intent:';
const DRAFT_PREFIX = 'reader-interactions:comment-draft:';
const TTL_MS = 15 * 60 * 1000;
const ALLOWED_ACTIONS = new Set(['comment', 'download', 'share', 'copy']);

function read(key) {
    try {
        const value = JSON.parse(window.sessionStorage.getItem(key) || 'null');
        if (!value || value.expiresAt <= Date.now()) {
            window.sessionStorage.removeItem(key);
            return null;
        }
        return value;
    } catch (_error) {
        return null;
    }
}

function write(key, value) {
    try {
        window.sessionStorage.setItem(key, JSON.stringify(value));
    } catch (_error) {
        // Storage can be unavailable in privacy modes. The gate still works without recovery.
    }
}

function remove(key) {
    try {
        window.sessionStorage.removeItem(key);
    } catch (_error) {
        // Nothing sensitive should be copied to another storage fallback.
    }
}

function key(prefix, articleId, flowId = '') {
    return `${prefix}${articleId}:${flowId || 'none'}`;
}

function matchingKeys(prefix, articleId) {
    const result = [];
    try {
        for (let index = 0; index < window.sessionStorage.length; index += 1) {
            const candidate = window.sessionStorage.key(index);
            if (candidate?.startsWith(`${prefix}${articleId}:`)) result.push(candidate);
        }
    } catch (_error) {
        return result;
    }
    return result;
}

export function saveIntent(articleId, action, flowId = '', email = '') {
    if (!ALLOWED_ACTIONS.has(action)) return;
    write(key(INTENT_PREFIX, articleId, flowId), {
        articleId,
        action,
        flowId,
        email: String(email || '').slice(0, 254),
        expiresAt: Date.now() + TTL_MS,
    });
}

export function consumeIntent(articleId, flowId = '') {
    const keys = matchingKeys(INTENT_PREFIX, articleId);
    for (const intentKey of keys) {
        const value = read(intentKey);
        if (
            value &&
            value.articleId === articleId &&
            (!flowId || value.flowId === flowId) &&
            ALLOWED_ACTIONS.has(value.action)
        ) {
            remove(intentKey);
            return value.action;
        }
    }
    return null;
}

export function loadIntent(articleId) {
    const keys = matchingKeys(INTENT_PREFIX, articleId);
    for (const intentKey of keys.reverse()) {
        const value = read(intentKey);
        if (value && value.articleId === articleId && ALLOWED_ACTIONS.has(value.action)) {
            return value;
        }
    }
    return null;
}

export function clearIntent(articleId = '') {
    if (!articleId) return;
    for (const intentKey of matchingKeys(INTENT_PREFIX, articleId)) remove(intentKey);
}

export function saveDraft(articleId, body, flowId = '') {
    const draft = String(body || '').slice(0, 2000);
    if (!draft) return;
    write(key(DRAFT_PREFIX, articleId, flowId), {
        body: draft,
        flowId,
        expiresAt: Date.now() + TTL_MS,
    });
}

export function loadDraft(articleId, flowId = '') {
    const keys = matchingKeys(DRAFT_PREFIX, articleId);
    for (const draftKey of keys.reverse()) {
        const value = read(draftKey);
        if (value && (!flowId || value.flowId === flowId)) return value.body || '';
    }
    return '';
}

export function clearDraft(articleId) {
    for (const draftKey of matchingKeys(DRAFT_PREFIX, articleId)) remove(draftKey);
}
