import { operationId, request } from './client';
import { mountComments } from './comments';
import {
    clearDraft,
    clearIntent,
    consumeIntent,
    loadDraft,
    loadIntent,
    saveDraft,
    saveIntent,
} from './intent';

function canonicalUrl() {
    const canonical = document.querySelector('link[rel="canonical"]')?.href;
    if (canonical) return canonical;
    const url = new URL(window.location.href);
    url.hash = '';
    return url.href;
}

function returnPath() {
    return `${window.location.pathname}${window.location.search}#reader-interactions`;
}

function verificationPurpose(action) {
    return action === 'copy' ? 'share' : action;
}

class ReaderInteractionsController {
    constructor(root) {
        this.root = root;
        this.articleId = root.dataset.articleId;
        this.status = root.querySelector('[data-reader-actions-status]');
        this.shareButton = root.querySelector('[data-reader-share]');
        this.copyButton = root.querySelector('[data-reader-copy]');
        this.downloadButton = root.querySelector('[data-reader-download]');
        this.gate = root.querySelector('[data-reader-verification]');
        this.gateEmail = root.querySelector('[data-reader-verification-email]');
        this.gateSubmit = root.querySelector('[data-reader-verification-submit]');
        this.gateCancel = root.querySelector('[data-reader-verification-cancel]');
        this.pairing = root.querySelector('[data-reader-verification-pairing]');
        this.pairingCountdown = root.querySelector('[data-reader-verification-countdown]');
        this.pairingWaiting = root.querySelector('[data-reader-verification-waiting]');
        this.resendButton = root.querySelector('[data-reader-verification-resend]');
        this.cancelFlowButton = root.querySelector('[data-reader-verification-cancel-flow]');
        this.urlFallback = root.querySelector('[data-reader-share-url]');
        this.pendingAction = '';
        this.lastTrigger = null;
        this.session = { authenticated: false };
        this.capabilities = null;
        this.comments = null;
        this.deviceFlow = null;
        this.pollTimer = null;
        this.countdownTimer = null;
    }

    setStatus(message, error = false) {
        this.status.textContent = message;
        this.status.dataset.error = error ? 'true' : 'false';
    }

    hideUrlFallback() {
        this.urlFallback.hidden = true;
        this.urlFallback.value = '';
    }

    disableActions() {
        for (const button of [this.shareButton, this.copyButton, this.downloadButton]) {
            button.disabled = true;
        }
    }

    async initialize() {
        this.disableActions();
        this.bindActions();
        try {
            this.session = (await request('/session/')).data;
            this.capabilities = (
                await request(`/articles/${this.articleId}/capabilities/`)
            ).data;
            const pageRelease = this.root.dataset.release;
            if (
                pageRelease &&
                this.capabilities.active_release &&
                pageRelease !== this.capabilities.active_release
            ) {
                throw Object.assign(new Error('Release mismatch'), { code: 'stale_version' });
            }
            this.configureActions();
            const recovered = this.session.authenticated
                ? consumeIntent(this.articleId)
                : null;
            const draft = loadDraft(this.articleId);
            this.comments = mountComments(this.root, {
                session: this.session,
                capabilities: this.capabilities,
                draft,
                onVerificationRequired: (action, details) =>
                    this.beginVerification(action, details),
                onDraftSubmitted: () => clearDraft(this.articleId),
            });
            if (!this.session.authenticated) {
                const pending = loadIntent(this.articleId);
                if (pending?.flowId) {
                    this.pendingAction = pending.action;
                    this.gate.hidden = false;
                    this.gateEmail.value = pending.email || '';
                    this.pairing.hidden = false;
                    this.pairingWaiting.textContent = 'Waiting for the email link. This computer will unlock automatically.';
                    this.deviceFlow = {
                        id: pending.flowId,
                        expiresAt: pending.expiresAt,
                        interval: 5000,
                        email: this.gateEmail.value,
                    };
                    this.startPolling();
                }
            }
            if (recovered) this.restoreIntent(recovered);
        } catch (_error) {
            this.disableActions();
            this.setStatus('Reader actions are temporarily unavailable.', true);
        }
    }

    bindActions() {
        this.shareButton.addEventListener('click', (event) => this.share(event));
        this.copyButton.addEventListener('click', (event) => this.copy(event));
        this.downloadButton.addEventListener('click', (event) => this.download(event));
        this.gate.addEventListener('submit', (event) => this.requestVerification(event));
        this.gateCancel.addEventListener('click', () => this.cancelVerification());
        this.resendButton?.addEventListener('click', () => this.resendVerification());
        this.cancelFlowButton?.addEventListener('click', () => this.cancelVerification());
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden && this.deviceFlow) this.pollDeviceFlow(true);
        });
    }

    configureActions() {
        let shareSupported = typeof navigator.share === 'function';
        if (shareSupported && typeof navigator.canShare === 'function') {
            try {
                shareSupported = navigator.canShare({
                    title: document.title,
                    url: canonicalUrl(),
                });
            } catch (_error) {
                shareSupported = false;
            }
        }
        this.shareButton.hidden = !shareSupported;
        this.shareButton.disabled = !this.capabilities.share_available;
        this.copyButton.disabled = !this.capabilities.share_available;
        this.downloadButton.disabled = !this.capabilities.pdf_available;
    }

    hasAccess(action, trigger) {
        if (!this.session.authenticated) {
            this.lastTrigger = trigger;
            this.beginVerification(action);
            return false;
        }
        if (
            (action === 'download' && !this.capabilities.can_download) ||
            ((action === 'share' || action === 'copy') && !this.capabilities.can_share)
        ) {
            this.setStatus('This reader action is unavailable.', true);
            return false;
        }
        return true;
    }

    beginVerification(action, details = {}) {
        if (details.draft) saveDraft(this.articleId, details.draft);
        if (details.trigger) this.lastTrigger = details.trigger;
        saveIntent(this.articleId, action);
        this.pendingAction = action;
        this.gate.hidden = false;
        if (this.pairing) this.pairing.hidden = true;
        this.setStatus('Email verification required.');
        this.gateEmail.focus();
        return false;
    }

    cancelVerification() {
        const flow = this.deviceFlow;
        this.stopPolling();
        this.gate.hidden = true;
        if (this.pairing) this.pairing.hidden = true;
        this.pendingAction = '';
        clearIntent(this.articleId);
        this.setStatus('Email verification cancelled.');
        this.lastTrigger?.focus();
        if (flow) {
            request(`/device-flows/${flow.id}/cancel/`, { method: 'POST', body: '{}' }).catch(() => {});
            this.deviceFlow = null;
        }
    }

    async requestVerification(event) {
        event.preventDefault();
        const action = this.pendingAction || 'share';
        const email = this.gateEmail.value;
        this.gateSubmit.disabled = true;
        try {
            const result = await request('/email-verifications/', {
                method: 'POST',
                body: JSON.stringify({
                    email,
                    intent: verificationPurpose(action),
                    return_to: returnPath(),
                }),
            });
            if (!result.data.flow_id) {
                this.setStatus('The verification request could not be started. Check the email address and try again.', true);
                return;
            }
            clearIntent(this.articleId);
            saveIntent(this.articleId, action, result.data.flow_id, email);
            const draft = loadDraft(this.articleId);
            if (draft) saveDraft(this.articleId, draft, result.data.flow_id);
            this.deviceFlow = {
                id: result.data.flow_id,
                expiresAt: Date.now() + (result.data.expires_in || 900) * 1000,
                interval: Math.max(1, result.data.interval || 5) * 1000,
                email,
            };
            this.pairing.hidden = false;
            this.gateEmail.value = email;
            this.setStatus('Verification email sent. Open it to unlock this computer automatically.');
            this.startPolling();
        } catch (_error) {
            this.setStatus('Email verification is temporarily unavailable.', true);
        } finally {
            this.gateSubmit.disabled = false;
        }
    }

    resendVerification() {
        const event = { preventDefault: () => {} };
        this.stopPolling();
        this.deviceFlow = null;
        this.requestVerification(event);
    }

    startPolling() {
        this.stopPolling();
        this.updateCountdown();
        this.countdownTimer = window.setInterval(() => this.updateCountdown(), 1000);
        this.pollDeviceFlow(true);
    }

    stopPolling() {
        if (this.pollTimer) window.clearTimeout(this.pollTimer);
        if (this.countdownTimer) window.clearInterval(this.countdownTimer);
        this.pollTimer = null;
        this.countdownTimer = null;
    }

    updateCountdown() {
        if (!this.deviceFlow || !this.pairingCountdown) return;
        const remaining = Math.max(0, Math.ceil((this.deviceFlow.expiresAt - Date.now()) / 1000));
        this.pairingCountdown.textContent = remaining
            ? `Expires in ${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')}.`
            : 'This verification has expired.';
        if (!remaining) {
            this.stopPolling();
            this.pairingWaiting.textContent = 'Verification expired. You can request a new email.';
        }
    }

    async pollDeviceFlow(immediate = false) {
        if (!this.deviceFlow || document.hidden) return;
        if (!immediate) await new Promise((resolve) => window.setTimeout(resolve, 0));
        const flow = this.deviceFlow;
        try {
            const result = await request(`/device-flows/${flow.id}/status/`);
            const state = result.data.status;
            if (state === 'approved') {
                await this.claimDeviceFlow();
                return;
            }
            if (['claimed', 'expired', 'cancelled', 'superseded', 'denied'].includes(state)) {
                this.stopPolling();
                this.pairingWaiting.textContent = state === 'claimed'
                    ? 'This computer is already verified.'
                    : 'Verification is no longer active. Request a new email.';
                return;
            }
            this.pairingWaiting.textContent = 'Waiting for the email link. This computer will unlock automatically.';
            this.pollTimer = window.setTimeout(() => this.pollDeviceFlow(), Math.max(1, result.data.retry_after || flow.interval / 1000) * 1000);
        } catch (_error) {
            this.stopPolling();
            this.setStatus('Verification status could not be checked. Request a new email.', true);
        }
    }

    async claimDeviceFlow() {
        const flow = this.deviceFlow;
        if (!flow) return;
        this.stopPolling();
        try {
            await request(`/device-flows/${flow.id}/claim/`, { method: 'POST', body: '{}' });
            this.session = (await request('/session/')).data;
            this.capabilities = (await request(`/articles/${this.articleId}/capabilities/`)).data;
            this.comments?.updateAccess(
                this.session,
                this.capabilities,
                loadDraft(this.articleId, flow.id) || loadDraft(this.articleId),
            );
            const recovered = consumeIntent(this.articleId, flow.id) || this.pendingAction;
            this.gate.hidden = true;
            this.pairing.hidden = true;
            this.deviceFlow = null;
            this.pendingAction = '';
            this.configureActions();
            this.setStatus('Email verified. Review your draft and submit when ready.');
            if (recovered) this.restoreIntent(recovered);
        } catch (_error) {
            this.setStatus('Verification was approved but could not be claimed. Try again.', true);
            this.deviceFlow = flow;
            this.startPolling();
        }
    }

    restoreIntent(action) {
        this.setStatus('Email verified. Choose the action again.');
        const focus = () => window.setTimeout(() => {
            if (action === 'comment') {
                this.comments?.focusComposer();
            } else if (action === 'download') {
                this.downloadButton.focus();
            } else if (action === 'copy') {
                this.copyButton.focus();
            } else if (!this.shareButton.hidden) {
                this.shareButton.focus();
            } else {
                this.copyButton.focus();
            }
        }, 0);
        if (this.comments?.ready) this.comments.ready.finally(focus);
        else focus();
    }

    share(event) {
        if (!this.hasAccess('share', event.currentTarget)) return;
        this.hideUrlFallback();
        let result;
        try {
            // This call must remain before every await/fetch to retain user activation.
            result = navigator.share({ title: document.title, url: canonicalUrl() });
        } catch (_error) {
            this.setStatus('System sharing failed.', true);
            void this.reportShare('system_share', 'failed');
            return;
        }
        Promise.resolve(result).then(
            () => {
                this.setStatus('System share completed.');
                void this.reportShare('system_share', 'completed');
            },
            (error) => {
                const cancelled = error?.name === 'AbortError';
                this.setStatus(cancelled ? 'System share cancelled.' : 'System sharing failed.', !cancelled);
                void this.reportShare('system_share', cancelled ? 'cancelled' : 'failed');
            },
        );
    }

    async copy(event) {
        if (!this.hasAccess('copy', event.currentTarget)) return;
        this.hideUrlFallback();
        try {
            if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
            await navigator.clipboard.writeText(canonicalUrl());
            this.setStatus('Link copied.');
            void this.reportShare('copy_link', 'completed');
        } catch (_error) {
            this.setStatus('Link could not be copied.', true);
            this.urlFallback.value = canonicalUrl();
            this.urlFallback.hidden = false;
            this.urlFallback.focus();
            this.urlFallback.select();
            void this.reportShare('copy_link', 'failed');
        }
    }

    async download(event) {
        if (!this.hasAccess('download', event.currentTarget)) return;
        this.downloadButton.disabled = true;
        try {
            const result = await request(`/articles/${this.articleId}/download-grants/`, {
                method: 'POST',
                headers: { 'Idempotency-Key': operationId() },
                body: '{}',
            });
            this.setStatus('PDF download ready.');
            window.location.assign(result.data.download_url);
        } catch (_error) {
            this.setStatus('PDF download is temporarily unavailable.', true);
        } finally {
            this.downloadButton.disabled = !this.capabilities.pdf_available;
        }
    }

    async reportShare(action, outcome) {
        try {
            await request(`/articles/${this.articleId}/share-events/`, {
                method: 'POST',
                headers: { 'Idempotency-Key': operationId() },
                body: JSON.stringify({ action, outcome }),
            });
        } catch (_error) {
            // Share event delivery is intentionally best effort and never blocks the action.
        }
    }
}

export function mountReaderInteractions(root) {
    return new ReaderInteractionsController(root).initialize();
}
