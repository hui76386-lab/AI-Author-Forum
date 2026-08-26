import { element, operationId, request } from './client';

function actionButton(label, action) {
    const button = element('button', 'c-reader-comments__action', label);
    button.type = 'button';
    button.addEventListener('click', action);
    return button;
}

function formatTime(value, locale) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '' : date.toLocaleString(locale || undefined);
}

class CommentsController {
    constructor(root, options = {}) {
        this.root = root;
        this.section = root.querySelector('[data-reader-comments]') || root;
        this.articleId = root.dataset.articleId;
        this.locale = root.dataset.locale || document.documentElement.lang;
        this.content = root.querySelector('[data-reader-interactions-root]');
        this.status = root.querySelector('[data-reader-interactions-status]');
        this.items = [];
        this.nextCursor = null;
        this.etag = '';
        this.session = options.session || null;
        this.capabilities = options.capabilities || null;
        this.draft = options.draft || '';
        this.onVerificationRequired = options.onVerificationRequired;
        this.onDraftSubmitted = options.onDraftSubmitted;
        this.composer = null;
    }

    setStatus(message, error = false) {
        this.status.textContent = message;
        this.status.dataset.error = error ? 'true' : 'false';
    }

    async initialize() {
        try {
            if (!this.session) this.session = (await request('/session/')).data;
            if (!this.capabilities) {
                this.capabilities = (
                    await request(`/articles/${this.articleId}/capabilities/`)
                ).data;
            }
            if (this.capabilities.comments_mode === 'hidden') {
                this.section.hidden = true;
                return;
            }
            await this.loadComments(true);
        } catch (error) {
            this.setStatus('Comments are temporarily unavailable.', true);
        }
    }

    async loadComments(reset = false) {
        const query = new URLSearchParams({ limit: '20' });
        if (!reset && this.nextCursor) query.set('cursor', this.nextCursor);
        const headers = {};
        if (reset && this.etag) headers['If-None-Match'] = this.etag;
        const result = await request(
            `/articles/${this.articleId}/comments/?${query.toString()}`,
            { headers },
        );
        if (result.notModified) return;
        this.etag = result.response.headers.get('ETag') || '';
        this.items = reset ? result.data.items : this.items.concat(result.data.items);
        this.nextCursor = result.data.next_cursor;
        this.render();
    }

    render() {
        this.content.replaceChildren();
        if (this.capabilities.comments_mode === 'open') this.renderComposer();
        const list = element('ol', 'c-reader-comments__list');
        for (const comment of this.items) list.append(this.renderComment(comment, false));
        if (!this.items.length) {
            list.append(element('li', 'c-reader-comments__empty', 'No comments yet.'));
        }
        this.content.append(list);
        if (this.nextCursor) {
            this.content.append(
                actionButton('Load more comments', async (event) => {
                    event.currentTarget.disabled = true;
                    try {
                        await this.loadComments(false);
                    } catch (_error) {
                        this.setStatus('More comments could not be loaded.', true);
                        event.currentTarget.disabled = false;
                    }
                }),
            );
        }
    }

    renderComposer() {
        if (!this.session.authenticated && !this.onVerificationRequired) {
            this.content.append(this.verificationForm());
            return;
        }
        const form = this.commentForm('Add a comment', async (body) => {
            if (!this.session.authenticated) {
                return this.onVerificationRequired('comment', {
                    draft: body,
                    trigger: this.composer,
                });
            }
            await this.write(`/articles/${this.articleId}/comments/`, {
                body,
                expected_policy_version: this.capabilities.policy_version,
            });
            this.onDraftSubmitted?.();
            return true;
        });
        this.content.append(form);
        this.composer = form.querySelector('textarea');
        if (this.draft) {
            this.composer.value = this.draft;
            this.composer.dispatchEvent(new Event('input'));
        }
    }

    verificationForm() {
        const form = element('form', 'c-reader-comments__form');
        const label = element('label', '', 'Email');
        const input = element('input');
        input.type = 'email';
        input.name = 'email';
        input.required = true;
        input.autocomplete = 'email';
        label.append(input);
        const button = element('button', 'c-reader-comments__submit', 'Verify email');
        button.type = 'submit';
        form.append(label, button);
        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            button.disabled = true;
            try {
                await request('/email-verifications/', {
                    method: 'POST',
                    body: JSON.stringify({
                        email: input.value,
                        intent: 'comment',
                        return_to: `${window.location.pathname}#reader-interactions`,
                    }),
                });
                this.setStatus('Check your email to continue.');
            } catch (_error) {
                this.setStatus('Verification is temporarily unavailable.', true);
            } finally {
                button.disabled = false;
            }
        });
        return form;
    }

    commentForm(labelText, submit, cancel) {
        const form = element('form', 'c-reader-comments__form');
        const id = `reader-comment-${operationId()}`;
        const label = element('label', '', labelText);
        label.htmlFor = id;
        const textarea = element('textarea');
        textarea.id = id;
        textarea.required = true;
        textarea.maxLength = 2000;
        textarea.rows = 4;
        const count = element('span', 'c-reader-comments__count', '0 / 2000');
        count.setAttribute('aria-live', 'polite');
        textarea.addEventListener('input', () => {
            count.textContent = `${textarea.value.length} / 2000`;
        });
        const controls = element('div', 'c-reader-comments__controls');
        const button = element('button', 'c-reader-comments__submit', 'Submit');
        button.type = 'submit';
        controls.append(button);
        if (cancel) controls.append(actionButton('Cancel', cancel));
        form.append(label, textarea, count, controls);
        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            button.disabled = true;
            try {
                const completed = await submit(textarea.value);
                if (completed !== false) {
                    form.reset();
                    count.textContent = '0 / 2000';
                }
            } catch (error) {
                this.setStatus(this.errorMessage(error), true);
            } finally {
                button.disabled = false;
            }
        });
        return form;
    }

    renderComment(comment, reply) {
        const item = element('li', reply ? 'c-reader-comments__reply' : 'c-reader-comments__comment');
        const header = element('div', 'c-reader-comments__meta');
        header.append(element('strong', '', comment.author.display_name));
        const time = element('time', '', formatTime(comment.created_at, this.locale));
        time.dateTime = comment.created_at;
        header.append(time);
        item.append(header);
        const body = element(
            'p',
            'c-reader-comments__body',
            comment.withdrawn ? 'This comment was withdrawn by its author.' : comment.body,
        );
        item.append(body);
        if (comment.state === 'pending') {
            item.append(element('p', 'c-reader-comments__state', 'Awaiting review'));
        }
        const actions = element('div', 'c-reader-comments__controls');
        if (!reply && this.capabilities.can_comment && this.session.authenticated && !comment.withdrawn) {
            actions.append(actionButton('Reply', (event) => this.openReply(item, comment, event.currentTarget)));
        }
        if (comment.owned_by_viewer && !comment.withdrawn && comment.state !== 'pending') {
            actions.append(
                actionButton('Withdraw', async () => {
                    try {
                        await this.write(
                            `/articles/${this.articleId}/comments/${comment.id}/withdrawal/`,
                            { expected_version: comment.version },
                        );
                    } catch (error) {
                        this.setStatus(this.errorMessage(error), true);
                    }
                }),
            );
        } else if (this.session.authenticated && comment.state === 'published') {
            actions.append(actionButton('Report', (event) => this.openReport(item, comment, event.currentTarget)));
        }
        if (actions.childElementCount) item.append(actions);
        if (comment.replies?.length) {
            const replies = element('ol', 'c-reader-comments__replies');
            for (const child of comment.replies) replies.append(this.renderComment(child, true));
            item.append(replies);
        }
        return item;
    }

    openReply(container, comment, trigger) {
        if (container.querySelector('[data-inline-form]')) return;
        const holder = element('div');
        holder.dataset.inlineForm = 'reply';
        const close = () => {
            holder.remove();
            trigger.focus();
        };
        holder.append(
            this.commentForm(
                `Reply to ${comment.author.display_name}`,
                async (body) => {
                    await this.write(
                        `/articles/${this.articleId}/comments/${comment.id}/replies/`,
                        { body, expected_policy_version: this.capabilities.policy_version },
                    );
                    close();
                },
                close,
            ),
        );
        container.append(holder);
        holder.querySelector('textarea').focus();
    }

    openReport(container, comment, trigger) {
        if (container.querySelector('[data-inline-form]')) return;
        const form = element('form', 'c-reader-comments__form c-reader-comments__form--inline');
        form.dataset.inlineForm = 'report';
        const label = element('label', '', 'Reason');
        const select = element('select');
        for (const [value, text] of [
            ['spam', 'Spam'],
            ['harassment', 'Harassment'],
            ['hate', 'Hate'],
            ['privacy', 'Privacy'],
            ['misinformation', 'Misinformation'],
            ['other', 'Other'],
        ]) {
            const option = element('option', '', text);
            option.value = value;
            select.append(option);
        }
        label.append(select);
        const submit = element('button', 'c-reader-comments__submit', 'Submit report');
        submit.type = 'submit';
        const close = () => {
            form.remove();
            trigger.focus();
        };
        form.append(label, submit, actionButton('Cancel', close));
        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            submit.disabled = true;
            try {
                await request(
                    `/articles/${this.articleId}/comments/${comment.id}/reports/`,
                    {
                        method: 'POST',
                        headers: { 'Idempotency-Key': operationId() },
                        body: JSON.stringify({ reason: select.value, details: '' }),
                    },
                );
                this.setStatus('Report received.');
                close();
            } catch (error) {
                this.setStatus(this.errorMessage(error), true);
                submit.disabled = false;
            }
        });
        container.append(form);
        select.focus();
    }

    async write(path, payload) {
        const result = await request(path, {
            method: 'POST',
            headers: { 'Idempotency-Key': operationId() },
            body: JSON.stringify(payload),
        });
        this.setStatus(
            result.data.state === 'pending' ? 'Comment is awaiting review.' : 'Comment updated.',
        );
        this.etag = '';
        await this.loadComments(true);
        return result.data;
    }

    errorMessage(error) {
        const messages = {
            authentication_required: 'Verify your email to continue.',
            comments_closed: 'Comments are closed for this article.',
            comments_hidden: 'Comments are unavailable for this article.',
            stale_policy: 'Comment settings changed. Reload and try again.',
            invalid_comment: 'Review the comment and try again.',
            rate_limited: `Too many requests. Try again${error.retryAfter ? ` in ${error.retryAfter} seconds` : ' later'}.`,
            already_reported: 'You have already reported this comment.',
        };
        return messages[error.code] || 'The request could not be completed.';
    }

    focusComposer() {
        this.composer?.focus();
    }

    updateAccess(session, capabilities, draft = this.draft) {
        this.session = session;
        this.capabilities = capabilities;
        this.draft = draft || '';
        this.render();
    }
}

export function mountComments(root, options = {}) {
    const controller = new CommentsController(root, options);
    controller.ready = controller.initialize();
    return controller;
}
