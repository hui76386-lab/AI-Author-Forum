(() => {
  const csrf = () => document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
  const workbench = document.querySelector('[data-placement-workbench]');

  if (workbench) {
    const form = workbench.querySelector('[data-placement-form]');
    const article = form.querySelector('[name=article]');
    const target = form.querySelector('[name=target]');
    const slot = form.querySelector('[name=slot]');
    const starts = form.querySelector('[name=starts_at]');
    const ends = form.querySelector('[name=ends_at]');
    const title = form.querySelector('[name=override_title]');
    const summary = form.querySelector('[name=override_summary]');
    const image = form.querySelector('[name=override_image]');
    const previewTitle = workbench.querySelector('[data-preview-title]');
    const previewSummary = workbench.querySelector('[data-preview-summary]');
    const previewImage = workbench.querySelector('[data-preview-image]');
    const previewTarget = workbench.querySelector('[data-preview-target]');
    const previewSlot = workbench.querySelector('[data-preview-slot]');
    const capacityMax = workbench.querySelector('[data-capacity-max]');
    const capacityCurrent = workbench.querySelector('[data-capacity-current]');
    const capacityRemaining = workbench.querySelector('[data-capacity-remaining]');
    const capacityMessage = workbench.querySelector('[data-capacity-message]');
    let capacityTimer;

    const selectedLabel = (field, fallback) => {
      const label = field?.selectedOptions?.[0]?.textContent?.trim();
      return field?.value && label ? label : fallback;
    };

    const updatePreview = () => {
      const articleText = selectedLabel(article, '选择文章后显示标题');
      previewTitle.textContent = title?.value.trim() || articleText;
      previewSummary.textContent = summary?.value.trim() || '摘要覆盖为空时，正式生成将使用文章原摘要。';
      previewTarget.textContent = selectedLabel(target, '选择投放目标');
      previewSlot.textContent = selectedLabel(slot, '选择版位');
      if (!image?.value) previewImage.src = workbench.dataset.previewFallback;
    };

    const resetCapacity = () => {
      capacityMax.textContent = '—';
      capacityCurrent.textContent = '—';
      capacityRemaining.textContent = '—';
      capacityMessage.textContent = '选择目标、版位和生效区间后自动检查容量。';
      capacityMessage.className = 'placement-field__hint';
    };

    const updateCapacity = async () => {
      if (!target?.value || !slot?.value) {
        resetCapacity();
        return;
      }

      const params = new URLSearchParams({ target: target.value, slot: slot.value });
      if (starts?.value) params.set('starts_at', starts.value);
      if (ends?.value) params.set('ends_at', ends.value);
      const placementId = form.querySelector('[name=placement_id]')?.value;
      if (placementId) params.set('placement_id', placementId);

      capacityMessage.textContent = '正在检查该目标与版位的可用容量…';
      capacityMessage.className = 'placement-field__hint';

      try {
        const response = await fetch(`${workbench.dataset.capacityUrl}?${params}`, {
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || '容量检查失败');

        capacityMax.textContent = data.max_items;
        capacityCurrent.textContent = data.current;
        capacityRemaining.textContent = data.remaining;
        capacityMessage.textContent = data.remaining > 0
          ? `${data.target} / ${data.slot} 在该时间区间仍可投放 ${data.remaining} 条。`
          : '该目标版位在此时间区间容量已满，保存将被拒绝。';
        capacityMessage.className = data.remaining > 0
          ? 'placement-field__hint placement-status--ok'
          : 'placement-field__hint placement-status--danger';
      } catch (error) {
        capacityMessage.textContent = error.message;
        capacityMessage.className = 'placement-field__hint placement-status--danger';
      }
    };

    [article, target, slot, title, summary, image].forEach((field) => {
      field?.addEventListener('input', updatePreview);
      field?.addEventListener('change', updatePreview);
    });
    [target, slot, starts, ends].forEach((field) => field?.addEventListener('change', () => {
      clearTimeout(capacityTimer);
      capacityTimer = setTimeout(updateCapacity, 150);
    }));

    updatePreview();
    updateCapacity();
  }

  const bulkWorkbench = document.querySelector('[data-bulk-placement]');
  if (bulkWorkbench) {
    const bulkForm = bulkWorkbench.querySelector('[data-bulk-placement-form]');
    const journal = bulkForm.querySelector('[name=journal]');
    const articles = bulkForm.querySelector('[name=articles]');
    const slot = bulkForm.querySelector('[name=slot]');
    const starts = bulkForm.querySelector('[name=starts_at]');
    const ends = bulkForm.querySelector('[name=ends_at]');
    const search = bulkForm.querySelector('[data-bulk-article-search]');
    const selectedCount = bulkForm.querySelector('[data-bulk-selected-count]');
    const submit = bulkForm.querySelector('[data-bulk-submit]');
    const articlesMessage = bulkForm.querySelector('[data-bulk-articles-message]');
    const capacityMessage = bulkForm.querySelector('[data-bulk-capacity-message]');
    let bulkCapacityTimer;
    let journalRefreshTimer;
    let articlesRequestController;

    const updateBulkSelection = () => {
      const count = articles?.selectedOptions?.length || 0;
      selectedCount.textContent = count;
      if (submit) submit.disabled = count === 0;
      const remaining = Number(bulkForm.querySelector('[data-bulk-capacity-remaining]')?.textContent);
      if (Number.isFinite(remaining) && count > remaining) {
        capacityMessage.textContent = `已选择 ${count} 篇，但当前仅剩 ${remaining} 个名额，请减少文章数量或调整生效区间。`;
        capacityMessage.className = 'placement-field__hint placement-status--danger';
      }
    };

    const updateBulkCapacity = async () => {
      if (!journal?.value || !slot?.value) return;
      const params = new URLSearchParams({ target: `journal:${journal.value}`, slot: slot.value });
      if (starts?.value) params.set('starts_at', starts.value);
      if (ends?.value) params.set('ends_at', ends.value);
      try {
        const response = await fetch(`${bulkWorkbench.dataset.capacityUrl}?${params}`, {
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || '容量检查失败');
        bulkForm.querySelector('[data-bulk-capacity-max]').textContent = data.max_items;
        bulkForm.querySelector('[data-bulk-capacity-current]').textContent = data.current;
        bulkForm.querySelector('[data-bulk-capacity-remaining]').textContent = data.remaining;
        capacityMessage.textContent = data.remaining > 0
          ? `该子期刊版位在此时间区间仍可投放 ${data.remaining} 篇。`
          : '该子期刊版位在此时间区间容量已满。';
        capacityMessage.className = data.remaining > 0
          ? 'placement-field__hint placement-status--ok'
          : 'placement-field__hint placement-status--danger';
        updateBulkSelection();
      } catch (error) {
        capacityMessage.textContent = error.message;
        capacityMessage.className = 'placement-field__hint placement-status--danger';
      }
    };

    const setArticlesMessage = (message, isError = false) => {
      if (!articlesMessage) return;
      articlesMessage.textContent = message;
      articlesMessage.className = isError
        ? 'placement-field__hint placement-status--danger'
        : 'placement-field__hint';
    };

    const loadJournalArticles = async () => {
      if (!articles) return;
      if (!journal?.value) {
        articles.replaceChildren();
        articles.disabled = true;
        setArticlesMessage('请先选择目标子期刊。');
        updateBulkSelection();
        return;
      }

      articlesRequestController?.abort();
      articlesRequestController = new AbortController();
      const requestedJournal = journal.value;
      articles.disabled = true;
      articles.replaceChildren();
      setArticlesMessage('正在加载该子期刊的可用文章…');
      updateBulkSelection();

      try {
        const params = new URLSearchParams({ journal: requestedJournal });
        const response = await fetch(`${bulkWorkbench.dataset.articlesUrl}?${params}`, {
          cache: 'no-store',
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
          signal: articlesRequestController.signal,
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || '文章列表加载失败');
        if (journal.value !== requestedJournal) return;

        articles.replaceChildren(...data.articles.map((item) => new Option(item.label, item.id)));
        if (search) search.value = '';
        setArticlesMessage(data.articles.length
          ? `已加载 ${data.articles.length} 篇可用文章，可按 Ctrl（Windows）或 Command（macOS）多选。`
          : '该子期刊暂无审核通过的可用文章。');
      } catch (error) {
        if (error.name === 'AbortError') return;
        articles.replaceChildren();
        setArticlesMessage(error.message || '文章列表加载失败，请重新选择子期刊后重试。', true);
      } finally {
        if (journal.value === requestedJournal) articles.disabled = false;
        updateBulkSelection();
      }
    };

    const refreshJournalSelection = () => {
      clearTimeout(journalRefreshTimer);
      journalRefreshTimer = setTimeout(() => {
        loadJournalArticles();
        updateBulkCapacity();
      }, 0);
    };

    journal?.addEventListener('input', refreshJournalSelection);
    journal?.addEventListener('change', refreshJournalSelection);
    articles?.addEventListener('change', updateBulkSelection);
    search?.addEventListener('input', () => {
      const query = search.value.trim().toLowerCase();
      [...articles.options].forEach((option) => {
        option.hidden = Boolean(query) && !option.textContent.toLowerCase().includes(query);
      });
    });
    [slot, starts, ends].forEach((field) => field?.addEventListener('change', () => {
      clearTimeout(bulkCapacityTimer);
      bulkCapacityTimer = setTimeout(updateBulkCapacity, 150);
    }));
    updateBulkSelection();
    loadJournalArticles();
    updateBulkCapacity();
    window.addEventListener('pageshow', (event) => {
      if (event.persisted) refreshJournalSelection();
    });
  }

  const table = document.querySelector('#current-placements[data-can-reorder="1"]');
  if (!table) return;

  table.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-move]');
    if (!button) return;

    const row = button.closest('[data-placement-id]');
    const rows = [...table.querySelectorAll('[data-placement-group="' + CSS.escape(row.dataset.placementGroup) + '"]')];
    const index = rows.indexOf(row);
    const nextIndex = button.dataset.move === 'up' ? index - 1 : index + 1;
    if (nextIndex < 0 || nextIndex >= rows.length) return;

    if (nextIndex < index) row.parentNode.insertBefore(row, rows[nextIndex]);
    else row.parentNode.insertBefore(rows[nextIndex], row);

    const ordered = [...table.querySelectorAll('[data-placement-group="' + CSS.escape(row.dataset.placementGroup) + '"]')];
    const body = new URLSearchParams();
    ordered.forEach((item) => body.append('placement_ids', item.dataset.placementId));

    const response = await fetch(table.dataset.reorderUrl, {
      method: 'POST',
      headers: { 'X-CSRFToken': csrf(), 'X-Requested-With': 'XMLHttpRequest' },
      body,
    });
    const data = await response.json();
    if (!response.ok) {
      alert(data.error || '排序失败');
      window.location.reload();
      return;
    }

    ordered.forEach((item, position) => {
      item.querySelector('[data-sort-order]').textContent = (position + 1) * 10;
    });
  });
})();
