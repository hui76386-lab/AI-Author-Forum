(() => {
  "use strict";

  const syncJournalCategories = () => {
    document.querySelectorAll("select[data-journal-source]").forEach((category) => {
      const journal = document.getElementById(category.dataset.journalSource);
      if (!journal) return;
      const journalId = journal.value;
      let selectedIsValid = !category.value;
      Array.from(category.options).forEach((option) => {
        if (!option.value) return;
        const matches = Boolean(journalId) && option.dataset.journalId === journalId;
        option.hidden = !matches;
        option.disabled = !matches;
        if (option.selected && matches) selectedIsValid = true;
      });
      if (!selectedIsValid) category.value = "";
      if (!journal.dataset.categoryListener) {
        journal.addEventListener("change", syncJournalCategories);
        journal.dataset.categoryListener = "true";
      }
    });
  };

  const initialiseJournalPicker = () => {
    document.querySelectorAll("[data-journal-picker]").forEach((picker) => {
      const filter = picker.querySelector("[data-journal-filter]");
      const select = picker.querySelector("select");
      const count = picker.querySelector("[data-journal-count]");
      if (!filter || !select) return;
      const apply = () => {
        const query = filter.value.trim().toLocaleLowerCase();
        let visible = 0;
        Array.from(select.options).forEach((option) => {
          if (!option.value) return;
          const matches = !query || option.text.toLocaleLowerCase().includes(query);
          option.hidden = !matches && !option.selected;
          option.disabled = !matches && !option.selected;
          if (matches) visible += 1;
        });
        if (count) {
          count.textContent = visible
            ? `找到 ${visible} 本当前可投稿期刊。`
            : "没有匹配的开放期刊，请调整搜索条件。";
        }
      };
      filter.addEventListener("input", apply);
      select.addEventListener("change", () => {
        apply();
        syncJournalCategories();
      });
      apply();
    });
  };

  const initialiseContributorFormsets = () => {
    document.querySelectorAll("[data-contributor-formset]").forEach((formset) => {
      const total = formset.querySelector('[name$="-TOTAL_FORMS"]');
      const maximum = formset.querySelector('[name$="-MAX_NUM_FORMS"]');
      const forms = formset.querySelector("[data-contributor-forms]");
      const template = formset.querySelector("[data-contributor-template]");
      const add = formset.querySelector("[data-add-contributor]");
      if (!total || !maximum || !forms || !template || !add) return;
      const updateButton = () => {
        add.disabled = Number(total.value) >= Number(maximum.value);
      };
      add.addEventListener("click", () => {
        if (add.disabled) return;
        const index = Number(total.value);
        forms.insertAdjacentHTML(
          "beforeend",
          template.innerHTML.replaceAll("__prefix__", String(index)),
        );
        total.value = String(index + 1);
        updateButton();
      });
      updateButton();
    });
  };

  const editorId = () =>
    `block_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;

  const newBlock = (type) => {
    const id = editorId();
    const common = { id, type };
    if (type === "paragraph") return { ...common, html: "<p></p>" };
    if (type === "heading") return { ...common, text: "" };
    if (type === "image") {
      return { ...common, image_asset_id: null, alt_text: "", caption: "" };
    }
    if (type === "quote") return { ...common, quote: "", attribution: "" };
    if (type === "list") {
      return { ...common, list_type: "unordered", items: ["<p></p>"] };
    }
    if (type === "table") {
      return {
        ...common,
        data: [["", ""], ["", ""]],
        first_row_is_table_header: true,
        first_col_is_header: false,
        caption: "",
      };
    }
    return { ...common, document_asset_id: null, link_text: "", description: "" };
  };

  const blockTitles = {
    paragraph: "正文段落",
    heading: "章节标题",
    image: "图片与说明",
    quote: "引用",
    list: "列表",
    table: "表格",
    document: "附件 / 文档",
  };

  const makeElement = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };

  const makeAction = (text, action, title) => {
    const button = makeElement("button", "author-body-block__command", text);
    button.type = "button";
    button.dataset.blockAction = action;
    button.title = title;
    return button;
  };

  const makeInput = (block, key, labelText, options = {}) => {
    const label = makeElement("label", "author-body-field", labelText);
    const field = document.createElement(options.multiline ? "textarea" : "input");
    field.dataset.bodyField = key;
    field.value = block[key] || "";
    if (options.type) field.type = options.type;
    if (options.maxLength) field.maxLength = options.maxLength;
    if (options.placeholder) field.placeholder = options.placeholder;
    if (options.rows) field.rows = options.rows;
    label.append(field);
    return label;
  };

  const makeRichEditor = (block, key, labelText, itemIndex = null) => {
    const wrapper = makeElement("div", "author-rich-field");
    const label = makeElement("span", "author-body-field__label", labelText);
    const toolbar = makeElement("div", "author-rich-toolbar");
    [
      ["B", "bold", "加粗"],
      ["I", "italic", "斜体"],
      ["Link", "createLink", "插入安全链接"],
      ["List", "insertUnorderedList", "无序列表"],
    ].forEach(([text, command, title]) => {
      const button = makeElement("button", "author-rich-toolbar__button", text);
      button.type = "button";
      button.dataset.richCommand = command;
      button.title = title;
      toolbar.append(button);
    });
    const editor = makeElement("div", "author-rich-editor");
    editor.contentEditable = "true";
    editor.dataset.richEditor = key;
    if (itemIndex !== null) editor.dataset.listIndex = String(itemIndex);
    // The initial value has already passed the server-side Wagtail allowlist.
    editor.innerHTML = block[key] || "<p></p>";
    wrapper.append(label, toolbar, editor);
    return wrapper;
  };

  const initialiseBodyEditors = () => {
    document.querySelectorAll("[data-body-editor]").forEach((editor) => {
      const input = document.getElementById(editor.dataset.bodyInput);
      const blockContainer = editor.querySelector("[data-body-blocks]");
      const empty = editor.querySelector("[data-body-empty]");
      if (!input || !blockContainer || !empty) return;
      let blocks = [];
      const fileCache = new Map();
      try {
        const parsed = JSON.parse(input.value || "[]");
        if (Array.isArray(parsed)) {
          blocks = parsed
            .filter((block) => block && typeof block === "object" && block.type)
            .map((block) => ({ ...newBlock(block.type), ...block, id: block.id || editorId() }));
        }
      } catch (_) {
        blocks = [];
      }

      const sync = () => {
        input.value = JSON.stringify(blocks);
        empty.hidden = blocks.length > 0;
      };

      const findBlock = (element) =>
        blocks.find((block) => block.id === element.closest("[data-body-block]")?.dataset.bodyBlockId);

      const restoreCachedFile = (fileInput, id) => {
        const file = fileCache.get(id);
        if (!file || !window.DataTransfer) return;
        const transfer = new DataTransfer();
        transfer.items.add(file);
        fileInput.files = transfer.files;
      };

      const renderBody = (block, content) => {
        if (block.type === "paragraph") {
          content.append(makeRichEditor(block, "html", "正文段落"));
        } else if (block.type === "heading") {
          content.append(makeInput(block, "text", "章节标题", { maxLength: 160, placeholder: "例如：研究方法" }));
        } else if (block.type === "image") {
          const upload = makeElement("label", "author-body-field", "图片文件");
          const fileInput = document.createElement("input");
          fileInput.type = "file";
          fileInput.accept = "image/jpeg,image/png,image/webp";
          fileInput.name = `body_image_${block.id}`;
          fileInput.dataset.bodyFile = "image";
          restoreCachedFile(fileInput, block.id);
          upload.append(fileInput);
          content.append(upload, makeInput(block, "alt_text", "替代文本", { maxLength: 255 }), makeInput(block, "caption", "图片说明", { maxLength: 255 }));
          if (block.image_asset_id && !fileCache.has(block.id)) {
            content.append(makeElement("p", "author-body-block__retained", "将保留当前稿件中的图片；选择新文件可替换。"));
          }
        } else if (block.type === "quote") {
          content.append(makeInput(block, "quote", "引用内容", { multiline: true, rows: 4, placeholder: "输入引用内容" }), makeInput(block, "attribution", "出处 / 作者", { maxLength: 255 }));
        } else if (block.type === "list") {
          const listType = makeElement("label", "author-body-field", "列表类型");
          const select = document.createElement("select");
          select.dataset.bodyField = "list_type";
          [["unordered", "无序列表"], ["ordered", "有序列表"]].forEach(([value, text]) => {
            const option = new Option(text, value, false, block.list_type === value);
            select.append(option);
          });
          listType.append(select);
          content.append(listType);
          const items = makeElement("div", "author-list-items");
          (block.items || []).forEach((item, index) => {
            const itemBlock = { html: item };
            const itemRow = makeElement("div", "author-list-item");
            itemRow.append(makeRichEditor(itemBlock, "html", `列表项 ${index + 1}`, index));
            const remove = makeAction("x", "remove-list-item", "删除列表项");
            remove.dataset.listIndex = String(index);
            itemRow.append(remove);
            items.append(itemRow);
          });
          const add = makeAction("+ 添加列表项", "add-list-item", "添加列表项");
          content.append(items, add);
        } else if (block.type === "table") {
          const controls = makeElement("div", "author-table-controls");
          controls.append(makeAction("+ 行", "table-add-row", "添加表格行"), makeAction("+ 列", "table-add-column", "添加表格列"), makeAction("- 行", "table-remove-row", "删除最后一行"), makeAction("- 列", "table-remove-column", "删除最后一列"));
          const table = makeElement("div", "author-table-editor");
          (block.data || []).forEach((row, rowIndex) => {
            const rowElement = makeElement("div", "author-table-editor__row");
            row.forEach((cell, columnIndex) => {
              const cellInput = document.createElement("input");
              cellInput.value = cell;
              cellInput.maxLength = 1000;
              cellInput.dataset.tableRow = String(rowIndex);
              cellInput.dataset.tableColumn = String(columnIndex);
              cellInput.placeholder = `第 ${rowIndex + 1} 行`;
              rowElement.append(cellInput);
            });
            table.append(rowElement);
          });
          const options = makeElement("div", "author-table-options");
          [["first_row_is_table_header", "首行作为表头"], ["first_col_is_header", "首列作为表头"]].forEach(([key, text]) => {
            const label = makeElement("label", "author-check", text);
            const check = document.createElement("input");
            check.type = "checkbox";
            check.checked = Boolean(block[key]);
            check.dataset.bodyCheck = key;
            label.prepend(check);
            options.append(label);
          });
          content.append(controls, table, options, makeInput(block, "caption", "表格说明", { maxLength: 500 }));
        } else if (block.type === "document") {
          const upload = makeElement("label", "author-body-field", "附件文件");
          const fileInput = document.createElement("input");
          fileInput.type = "file";
          fileInput.accept = ".pdf,.doc,.docx,.xlsx,.csv,.txt";
          fileInput.name = `body_document_${block.id}`;
          fileInput.dataset.bodyFile = "document";
          restoreCachedFile(fileInput, block.id);
          upload.append(fileInput);
          content.append(upload, makeInput(block, "link_text", "链接文字", { maxLength: 160 }), makeInput(block, "description", "附件说明", { multiline: true, rows: 3, maxLength: 500 }));
          if (block.document_asset_id && !fileCache.has(block.id)) {
            content.append(makeElement("p", "author-body-block__retained", "将保留当前稿件中的附件；选择新文件可替换。"));
          }
        }
      };

      const render = () => {
        blockContainer.replaceChildren();
        blocks.forEach((block, index) => {
          const card = makeElement("section", "author-body-block");
          card.dataset.bodyBlock = "";
          card.dataset.bodyBlockId = block.id;
          const header = makeElement("header", "author-body-block__header");
          const title = makeElement("strong", "", blockTitles[block.type] || "正文内容");
          const position = makeElement("span", "author-body-block__position", `内容块 ${index + 1}`);
          const commands = makeElement("div", "author-body-block__commands");
          const up = makeAction("^", "move-up", "上移内容块");
          const down = makeAction("v", "move-down", "下移内容块");
          const remove = makeAction("x", "remove", "删除内容块");
          up.disabled = index === 0;
          down.disabled = index === blocks.length - 1;
          commands.append(up, down, remove);
          header.append(title, position, commands);
          const content = makeElement("div", "author-body-block__content");
          renderBody(block, content);
          card.append(header, content);
          blockContainer.append(card);
        });
        sync();
      };

      editor.addEventListener("click", (event) => {
        const add = event.target.closest("[data-add-body-block]");
        if (add) {
          blocks.push(newBlock(add.dataset.addBodyBlock));
          render();
          return;
        }
        const richButton = event.target.closest("[data-rich-command]");
        if (richButton) {
          const card = richButton.closest("[data-body-block]");
          const rich = card?.querySelector("[data-rich-editor]");
          if (!rich) return;
          rich.focus();
          let argument = null;
          if (richButton.dataset.richCommand === "createLink") {
            argument = window.prompt("请输入安全链接地址（https:// 或 mailto:）：", "https://");
            if (!argument) return;
          }
          document.execCommand(richButton.dataset.richCommand, false, argument);
          rich.dispatchEvent(new Event("input", { bubbles: true }));
          return;
        }
        const button = event.target.closest("[data-block-action]");
        if (!button) return;
        const block = findBlock(button);
        if (!block) return;
        const index = blocks.indexOf(block);
        const action = button.dataset.blockAction;
        if (action === "remove") blocks.splice(index, 1);
        else if (action === "move-up" && index > 0) [blocks[index - 1], blocks[index]] = [blocks[index], blocks[index - 1]];
        else if (action === "move-down" && index < blocks.length - 1) [blocks[index + 1], blocks[index]] = [blocks[index], blocks[index + 1]];
        else if (action === "add-list-item" && block.items.length < 50) block.items.push("<p></p>");
        else if (action === "remove-list-item" && block.items.length > 1) block.items.splice(Number(button.dataset.listIndex), 1);
        else if (action === "table-add-row" && block.data.length < 30) block.data.push(Array(block.data[0].length).fill(""));
        else if (action === "table-remove-row" && block.data.length > 1) block.data.pop();
        else if (action === "table-add-column" && block.data[0].length < 12) block.data.forEach((row) => row.push(""));
        else if (action === "table-remove-column" && block.data[0].length > 1) block.data.forEach((row) => row.pop());
        else return;
        render();
      });

      editor.addEventListener("input", (event) => {
        const block = findBlock(event.target);
        if (!block) return;
        if (event.target.matches("[data-rich-editor]")) {
          const listIndex = event.target.dataset.listIndex;
          if (listIndex !== undefined) block.items[Number(listIndex)] = event.target.innerHTML;
          else block[event.target.dataset.richEditor] = event.target.innerHTML;
        } else if (event.target.matches("[data-body-field]")) {
          block[event.target.dataset.bodyField] = event.target.value;
        } else if (event.target.matches("[data-table-row][data-table-column]")) {
          block.data[Number(event.target.dataset.tableRow)][Number(event.target.dataset.tableColumn)] = event.target.value;
        }
        sync();
      });

      editor.addEventListener("change", (event) => {
        const block = findBlock(event.target);
        if (!block) return;
        if (event.target.matches("[data-body-file]")) {
          const file = event.target.files?.[0];
          if (file) fileCache.set(block.id, file);
          else fileCache.delete(block.id);
        } else if (event.target.matches("[data-body-check]")) {
          block[event.target.dataset.bodyCheck] = event.target.checked;
        } else if (event.target.matches("[data-body-field]")) {
          block[event.target.dataset.bodyField] = event.target.value;
        }
        sync();
      });

      editor.closest("form")?.addEventListener("submit", sync);
      render();
    });
  };

  document.addEventListener("DOMContentLoaded", () => {
    syncJournalCategories();
    initialiseJournalPicker();
    initialiseContributorFormsets();
    initialiseBodyEditors();
  });
})();
