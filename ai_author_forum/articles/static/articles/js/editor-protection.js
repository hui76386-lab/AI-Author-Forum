(function () {
    "use strict";

    var script = document.currentScript;
    var capabilitiesUrl = script && script.dataset ? script.dataset.capabilitiesUrl : "";
    var editorForm = document.querySelector("#page-edit-form, form[data-edit-form]");
    var reviewForms = document.querySelectorAll("form[data-article-review-form]");
    var sensitiveName = /(csrf|password|passwd|token|secret|credential|api[_-]?key)/i;
    var dirty = false;
    var submitting = false;
    var tabId = window.crypto && window.crypto.randomUUID
        ? window.crypto.randomUUID()
        : String(Date.now()) + "-" + String(Math.random()).slice(2);

    function showNotice(message, kind) {
        var selector = '[data-article-editor-notice="' + kind + '"]';
        var existing = document.querySelector(selector);
        if (existing) {
            existing.textContent = message;
            return;
        }
        var notice = document.createElement("div");
        notice.className = "article-editor-notice article-editor-notice--" + kind;
        notice.dataset.articleEditorNotice = kind;
        notice.setAttribute("role", "status");
        notice.textContent = message;
        var target = document.querySelector("main, .content-wrapper, #main") || document.body;
        target.insertBefore(notice, target.firstChild);
    }

    function draftFieldNameIsSafe(name) {
        return Boolean(name) &&
            !sensitiveName.test(name) &&
            name !== "id" &&
            !/-(?:id|TOTAL_FORMS|INITIAL_FORMS|MIN_NUM_FORMS|MAX_NUM_FORMS)$/.test(name);
    }

    function fieldIsSafe(field) {
        if (!draftFieldNameIsSafe(field.name) || field.disabled) {
            return false;
        }
        var type = String(field.type || "").toLowerCase();
        return !["file", "password", "submit", "button", "reset", "image"].includes(type);
    }

    function serialiseForm(form) {
        var entries = [];
        Array.prototype.forEach.call(form.elements, function (field) {
            if (!fieldIsSafe(field)) {
                return;
            }
            var type = String(field.type || "").toLowerCase();
            if ((type === "checkbox" || type === "radio") && !field.checked) {
                return;
            }
            if (field.tagName === "SELECT" && field.multiple) {
                Array.prototype.forEach.call(field.selectedOptions, function (option) {
                    entries.push([field.name, option.value]);
                });
                return;
            }
            entries.push([field.name, field.value]);
        });
        return entries;
    }

    function signature(entries) {
        return JSON.stringify(entries.filter(function (entry) {
            return draftFieldNameIsSafe(entry[0]);
        }).slice().sort(function (left, right) {
            var leftValue = left[0] + "\u0000" + left[1];
            var rightValue = right[0] + "\u0000" + right[1];
            return leftValue.localeCompare(rightValue);
        }));
    }

    function restoreEntries(form, entries) {
        var grouped = {};
        entries.forEach(function (entry) {
            grouped[entry[0]] = grouped[entry[0]] || [];
            grouped[entry[0]].push(entry[1]);
        });
        Object.keys(grouped).forEach(function (name) {
            var fields = form.querySelectorAll('[name="' + CSS.escape(name) + '"]');
            Array.prototype.forEach.call(fields, function (field) {
                if (!fieldIsSafe(field)) {
                    return;
                }
                var values = grouped[name];
                var type = String(field.type || "").toLowerCase();
                if (type === "checkbox" || type === "radio") {
                    field.checked = values.includes(field.value);
                } else if (field.tagName === "SELECT" && field.multiple) {
                    Array.prototype.forEach.call(field.options, function (option) {
                        option.selected = values.includes(option.value);
                    });
                } else {
                    field.value = values[values.length - 1];
                }
                field.dispatchEvent(new Event("input", { bubbles: true }));
                field.dispatchEvent(new Event("change", { bubbles: true }));
            });
        });
    }

    function installDuplicateSubmitProtection(form) {
        var locked = false;
        var clickedButton = null;
        form.addEventListener("click", function (event) {
            var button = event.target.closest('button[type="submit"], input[type="submit"]');
            if (!button || !form.contains(button)) {
                return;
            }
            if (locked) {
                event.preventDefault();
                event.stopImmediatePropagation();
                return;
            }
            clickedButton = button;
        }, true);
        form.addEventListener("submit", function (event) {
            if (locked) {
                event.preventDefault();
                return;
            }
            locked = true;
            if (clickedButton && clickedButton.name) {
                var shadow = document.createElement("input");
                shadow.type = "hidden";
                shadow.name = clickedButton.name;
                shadow.value = clickedButton.value;
                form.appendChild(shadow);
            }
            window.setTimeout(function () {
                Array.prototype.forEach.call(
                    form.querySelectorAll('button[type="submit"], input[type="submit"]'),
                    function (button) {
                        button.disabled = true;
                        button.setAttribute("aria-disabled", "true");
                    }
                );
            }, 0);
        });
    }

    function installReviewProtection(form) {
        installDuplicateSubmitProtection(form);
        form.addEventListener("click", function (event) {
            var button = event.target.closest('[data-review-action], button[name="action"]');
            if (!button || button.value !== "reject") {
                return;
            }
            var comment = form.querySelector('[name="comment"]');
            if (comment && !comment.value.trim()) {
                event.preventDefault();
                comment.setCustomValidity("驳回意见必填。");
                comment.reportValidity();
                comment.focus();
            } else if (comment) {
                comment.setCustomValidity("");
            }
        }, true);
    }

    Array.prototype.forEach.call(reviewForms, installReviewProtection);

    if (!editorForm) {
        return;
    }

    var draftKey = "article-editor-draft:" + location.pathname;
    var pendingKey = draftKey + ":pending-save";
    var tabKey = draftKey + ":active-tab";
    var channel = "BroadcastChannel" in window
        ? new BroadcastChannel("article-editor:" + location.pathname)
        : null;

    function readJson(key) {
        try {
            return JSON.parse(localStorage.getItem(key) || "null");
        } catch (error) {
            return null;
        }
    }

    function saveDraft() {
        if (!dirty || submitting) {
            return;
        }
        var payload = {
            savedAt: new Date().toISOString(),
            path: location.pathname,
            fields: serialiseForm(editorForm)
        };
        try {
            localStorage.setItem(draftKey, JSON.stringify(payload));
            showNotice("本地草稿已于 " + new Date(payload.savedAt).toLocaleString() + " 保存。", "draft");
        } catch (error) {
            showNotice("本地草稿保存失败，请尽快正式保存。", "error");
        }
    }

    function clearSavedDraft() {
        localStorage.removeItem(draftKey);
        localStorage.removeItem(pendingKey);
    }

    function reconcilePendingSave() {
        var pending = readJson(pendingKey);
        var draft = readJson(draftKey);
        if (!pending || !draft) {
            return;
        }
        var successMessage = document.querySelector(
            ".messages .success, .w-message--success, [data-message-status=\"success\"]"
        );
        var serverMatchesDraft = signature(serialiseForm(editorForm)) === signature(draft.fields || []);
        if (successMessage || serverMatchesDraft) {
            clearSavedDraft();
        }
    }

    function offerDraftRestore() {
        var draft = readJson(draftKey);
        if (!draft || !Array.isArray(draft.fields)) {
            return;
        }
        if (signature(draft.fields) === signature(serialiseForm(editorForm))) {
            clearSavedDraft();
            return;
        }
        var savedTime = new Date(draft.savedAt).toLocaleString();
        var restore = window.confirm(
            "检测到 " + savedTime + " 保存的本地草稿。恢复会覆盖当前表单中对应字段，但不会自动提交，是否恢复？"
        );
        if (restore) {
            restoreEntries(editorForm, draft.fields);
            dirty = true;
            showNotice("已恢复本地草稿，请检查差异后正式保存。", "draft");
        } else if (window.confirm("是否删除这份本地草稿？")) {
            clearSavedDraft();
        }
    }

    function markDirty(event) {
        if (event && event.target && !fieldIsSafe(event.target)) {
            return;
        }
        dirty = true;
        if (channel) {
            channel.postMessage({ type: "dirty", tabId: tabId });
        }
    }

    editorForm.addEventListener("input", markDirty, true);
    editorForm.addEventListener("change", markDirty, true);
    installDuplicateSubmitProtection(editorForm);
    editorForm.addEventListener("submit", function () {
        submitting = true;
        try {
            localStorage.setItem(pendingKey, JSON.stringify({ submittedAt: new Date().toISOString() }));
        } catch (error) {
            // Submission continues even when browser storage is unavailable.
        }
        dirty = false;
    });

    window.addEventListener("beforeunload", function (event) {
        if (!dirty || submitting) {
            return;
        }
        event.preventDefault();
        event.returnValue = "您有尚未保存的文章修改。";
    });

    function warnAboutAnotherTab() {
        showNotice("检测到另一个标签页正在编辑同一篇文章。保存前请确认 revision，避免覆盖他人修改。", "conflict");
    }

    function heartbeat() {
        var active = readJson(tabKey);
        if (active && active.tabId !== tabId && Date.now() - active.seenAt < 30000) {
            warnAboutAnotherTab();
        }
        localStorage.setItem(tabKey, JSON.stringify({ tabId: tabId, seenAt: Date.now() }));
        if (channel) {
            channel.postMessage({ type: "heartbeat", tabId: tabId });
        }
    }

    window.addEventListener("storage", function (event) {
        if (event.key === tabKey && event.newValue) {
            var active = readJson(tabKey);
            if (active && active.tabId !== tabId) {
                warnAboutAnotherTab();
            }
        }
    });
    if (channel) {
        channel.addEventListener("message", function (event) {
            if (event.data && event.data.tabId !== tabId) {
                warnAboutAnotherTab();
            }
        });
    }
    window.addEventListener("unload", function () {
        var active = readJson(tabKey);
        if (active && active.tabId === tabId) {
            localStorage.removeItem(tabKey);
        }
        if (channel) {
            channel.close();
        }
    });

    function hideRawHtmlControls() {
        var candidates = document.querySelectorAll(
            '[data-streamfield-block-type="html"], [data-block-type="html"], [role="option"], [role="menuitem"]'
        );
        Array.prototype.forEach.call(candidates, function (element) {
            var blockType = element.getAttribute("data-streamfield-block-type") || element.getAttribute("data-block-type");
            var text = (element.textContent || "").trim().toLowerCase();
            if (blockType === "html" || text.indexOf("raw html") !== -1 || text === "html") {
                var chooserItem = element.closest(
                    '[role="option"], [role="menuitem"], li, .c-sf-add-panel__button'
                ) || element;
                chooserItem.hidden = true;
                chooserItem.setAttribute("aria-hidden", "true");
            }
        });
    }

    function startDraftTimer(seconds) {
        window.setInterval(saveDraft, Math.max(Number(seconds) || 30, 10) * 1000);
    }

    if (capabilitiesUrl) {
        fetch(capabilitiesUrl, { credentials: "same-origin", headers: { Accept: "application/json" } })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error("capabilities request failed");
                }
                return response.json();
            })
            .then(function (capabilities) {
                if (!capabilities.can_use_raw_html) {
                    hideRawHtmlControls();
                    new MutationObserver(hideRawHtmlControls).observe(document.body, { childList: true, subtree: true });
                }
                startDraftTimer(capabilities.autosave_seconds);
            })
            .catch(function () {
                hideRawHtmlControls();
                startDraftTimer(30);
            });
    } else {
        startDraftTimer(30);
    }

    reconcilePendingSave();
    offerDraftRestore();
    heartbeat();
    window.setInterval(heartbeat, 10000);
}());
