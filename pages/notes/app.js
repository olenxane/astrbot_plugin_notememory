const $ = (id) => document.getElementById(id);
const bridge = window.AstrBotPluginPage;
const state = { scope: "", layer: "all", page: 1, pages: 1, keyword: "", items: [], maxChars: 500, editing: null, deleting: null, generation: 0 };
const names = { short: "短期便签", archived: "归档便签", important: "重要便签" };
let noticeTimer;
let settingsData = null;

const settingGroups = [
  { title: "记忆与上下文", description: "控制便签容量、保留时间和展示方式。", keys: ["short_term_days", "max_note_chars", "injection_max_chars", "page_size", "command_max_chars"] },
  { title: "自动总结", description: "控制总结触发时机、批次和使用的模型。", keys: ["auto_summary", "summary_rounds", "summary_hours", "summary_provider_id", "summary_max_notes", "summary_note_chars", "summary_batch_chars", "summary_timeout"] },
  { title: "权限", description: "限制指令和 LLM 工具的管理能力。", keys: ["admin_only_manage", "bot_manage_important"] },
];

function notice(text, error = false) {
  clearTimeout(noticeTimer);
  $("notice").textContent = text;
  $("notice").classList.toggle("error", error);
  $("notice").hidden = false;
  noticeTimer = setTimeout(() => { $("notice").hidden = true; }, error ? 9000 : 4500);
}

async function action(button, work) {
  button.disabled = true;
  try { await work(); }
  catch (error) { notice(error.message || "操作失败", true); }
  finally { button.disabled = false; }
}

function scopeRequired() {
  if (!state.scope) throw new Error("请先载入一个用户。");
  return state.scope;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function dateText(timestamp) {
  return timestamp ? new Date(timestamp * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—";
}

function setLayer(layer) {
  state.layer = layer;
  state.page = 1;
  document.querySelectorAll(".tab").forEach((tab) => {
    const active = tab.dataset.layer === layer;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-pressed", String(active));
  });
}

function openEditor(note = null) {
  scopeRequired();
  state.editing = note?.id || null;
  $("editor-title").textContent = note ? "编辑便签" : "新建便签";
  $("editor-scope").textContent = `用户：${state.scope}${note ? ` · ID ${note.id}` : ""}`;
  $("note-layer").value = note?.layer || (state.layer === "all" ? "short" : state.layer);
  $("note-content").value = note?.content || "";
  $("note-tags").value = note?.tags || "";
  $("editor-error").hidden = true;
  updateCharCount();
  $("editor").showModal();
  $("note-content").focus();
}

function updateCharCount() {
  $("char-count").textContent = `${Array.from($("note-content").value).length} / ${state.maxChars}`;
}

function renderNotes(result) {
  state.items = result.items;
  state.pages = result.pages;
  $("notes").replaceChildren();
  $("result-count").textContent = `共 ${result.total} 条${state.keyword ? "匹配记忆" : "记忆"}`;
  $("page-label").textContent = `第 ${result.page} / ${result.pages} 页`;
  $("previous").disabled = result.page <= 1;
  $("next").disabled = result.page >= result.pages;
  if (!result.items.length) {
    const empty = element("div", "empty-state");
    empty.append(element("span", "empty-symbol", "▤"), element("h3", "", state.keyword ? "没有匹配的便签" : "这个书架还空着"), element("p", "", state.keyword ? "试试更短的关键词；多个词需要全部匹配。" : "写下一条记忆，或在完成对话后点击「立即总结」。"));
    $("notes").append(empty);
    return;
  }
  for (const note of result.items) {
    const card = element("article", `note-card ${note.layer}`);
    const top = element("div", "note-top");
    top.append(element("span", "badge", names[note.layer]), element("span", "note-id", note.id));
    card.append(top, element("p", "note-content", note.content));
    if (note.tags) card.append(element("p", "note-tags", `# ${note.tags}`));
    const meta = note.layer === "archived" ? `归档于 ${dateText(note.archived_at)}` : note.layer === "important" ? `更新于 ${dateText(note.updated_at)} · 永久保留` : `活跃于 ${dateText(note.last_active_at)}`;
    card.append(element("p", "note-meta", meta));
    const actions = element("div", "note-actions");
    const edit = element("button", "", "编辑");
    edit.addEventListener("click", () => openEditor(note));
    actions.append(edit);
    if (note.layer === "archived") {
      const recall = element("button", "", "召回短期");
      recall.addEventListener("click", () => action(recall, async () => {
        const scope = scopeRequired();
        await bridge.apiPost("recall", { scope, id: note.id });
        notice("已召回短期层，归档计时从现在重新开始。");
        await refresh();
      }));
      actions.append(recall);
    }
    const remove = element("button", "delete-action", "删除");
    remove.addEventListener("click", () => {
      state.deleting = note;
      $("delete-preview").textContent = note.content;
      $("delete-error").hidden = true;
      $("delete-dialog").showModal();
    });
    actions.append(remove);
    card.append(actions);
    $("notes").append(card);
  }
}

function renderStatus(status) {
  for (const layer of ["short", "archived", "important"]) $("count-" + layer).textContent = status.counts[layer];
  $("short-description").textContent = `最近 ${status.short_term_days} 天活跃的记忆`;
  const thresholds = [];
  if (status.summary_rounds) thresholds.push(`${status.summary_rounds} 轮`);
  if (status.summary_hours) thresholds.push(`${status.summary_hours} 小时`);
  const mode = status.auto_summary && thresholds.length ? `自动：${thresholds.join(" 或 ")}` : "自动总结未触发（已关闭或阈值均为 0）";
  $("summary-status").textContent = `待总结 ${status.pending_rounds} 轮 · ${mode}${status.last_summary_at ? ` · 上次 ${dateText(status.last_summary_at)}` : ""}`;
  $("summary-error").hidden = !status.last_error;
  $("summary-error").textContent = status.last_error ? `最近总结失败（原文保留）：${status.last_error}` : "";
}

async function refresh() {
  const scope = scopeRequired();
  const generation = ++state.generation;
  const [notes, status] = await Promise.all([
    bridge.apiGet("notes", { scope, layer: state.layer, page: state.page, page_size: 12, keyword: state.keyword }),
    bridge.apiGet("status", { scope }),
  ]);
  if (generation !== state.generation) return;
  if (state.page > notes.pages) {
    state.page = notes.pages;
    return refresh();
  }
  renderNotes(notes);
  renderStatus(status);
}

async function loadScopes() {
  const result = await bridge.apiGet("scopes");
  $("scope-options").replaceChildren();
  for (const item of result.items) {
    const option = document.createElement("option");
    option.value = item.scope;
    option.label = item.label || item.scope;
    $("scope-options").append(option);
  }
  state.maxChars = result.max_note_chars;
  $("note-content").maxLength = state.maxChars;
  if (!$("scope").value && result.items.length) $("scope").value = result.items[0].scope;
}

async function loadScope() {
  const scope = $("scope").value.trim();
  if (!scope) throw new Error("选择或填写用户标识后再载入。");
  state.scope = scope;
  state.page = 1;
  await refresh();
  for (const id of ["new-note", "summarize", "refresh-memory", "export"]) $(id).disabled = false;
  $("scope-hint").textContent = `正在管理：${scope}。此用户的所有对话共享这些便签。`;
}

function settingControl(key, definition, value) {
  const id = `setting-${key}`;
  if (definition.type === "bool") {
    const input = element("input");
    input.type = "checkbox";
    input.id = id;
    input.name = key;
    input.checked = value;
    return input;
  }
  if (definition._special === "select_provider") {
    const select = element("select");
    select.id = id;
    select.name = key;
    select.append(new Option("跟随用户最近使用的模型", ""));
    const providers = [...new Set(settingsData.providers)];
    if (value && !providers.includes(value)) providers.push(value);
    for (const provider of providers) select.append(new Option(provider, provider));
    select.value = value;
    return select;
  }
  const input = element("input");
  input.id = id;
  input.name = key;
  if (definition.type === "int" || definition.type === "float") {
    input.type = "number";
    input.required = true;
    input.step = definition.type === "float" ? "0.5" : "1";
    input.min = ["summary_rounds", "summary_hours"].includes(key) ? "0" : "1";
    if (key === "page_size") input.max = "20";
  } else {
    input.type = "text";
  }
  input.value = value;
  return input;
}

function settingField(key, definition, value) {
  const control = settingControl(key, definition, value);
  const title = definition.description || key;
  const hint = definition.hint || "";
  if (definition.type === "bool") {
    const field = element("label", "setting-field setting-toggle");
    field.htmlFor = control.id;
    const copy = element("span", "setting-toggle-copy");
    copy.append(element("span", "setting-name", title), element("span", "setting-hint", hint));
    field.append(copy, control);
    return field;
  }
  const field = element("div", "setting-field");
  const label = element("label", "setting-name", title);
  label.htmlFor = control.id;
  field.append(label, control, element("p", "setting-hint", hint));
  return field;
}

function renderSettings() {
  const { schema, values } = settingsData;
  const container = $("settings-groups");
  container.replaceChildren();
  const assigned = new Set(settingGroups.flatMap((group) => group.keys));
  const remaining = Object.keys(schema).filter((key) => !assigned.has(key));
  const groups = remaining.length
    ? [...settingGroups, { title: "其他设置", description: "", keys: remaining }]
    : settingGroups;
  for (const group of groups) {
    const section = element("section", "settings-group");
    const heading = element("div", "settings-group-heading");
    heading.append(element("h2", "", group.title), element("p", "", group.description));
    const fields = element("div", "settings-fields");
    for (const key of group.keys) {
      if (schema[key]) fields.append(settingField(key, schema[key], values[key]));
    }
    section.append(heading, fields);
    container.append(section);
  }
  $("save-settings").disabled = false;
  $("settings-error").hidden = true;
  $("settings-hint").textContent = "设置保存在插件配置中，不会影响已有便签。";
}

async function loadSettings() {
  settingsData = await bridge.apiGet("settings");
  renderSettings();
}

function collectSettings() {
  const values = {};
  for (const [key, definition] of Object.entries(settingsData.schema)) {
    const control = $(`setting-${key}`);
    values[key] = definition.type === "bool"
      ? control.checked
      : ["int", "float"].includes(definition.type) ? Number(control.value) : control.value;
  }
  return values;
}

async function switchView(view) {
  if (view === "settings" && !settingsData) await loadSettings();
  if (view === "memory" && state.scope) await refresh();
  for (const button of document.querySelectorAll("[data-view]")) {
    button.classList.toggle("active", button.dataset.view === view);
  }
  $("memory-view").hidden = view !== "memory";
  $("settings-view").hidden = view !== "settings";
}

for (const button of document.querySelectorAll("[data-view]")) {
  button.addEventListener("click", () => action(button, () => switchView(button.dataset.view)));
}
$("reload-settings").addEventListener("click", () => action($("reload-settings"), loadSettings));
$("settings-form").addEventListener("input", () => {
  $("settings-hint").textContent = "有未保存的修改。";
});
$("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("save-settings");
  button.disabled = true;
  $("settings-error").hidden = true;
  try {
    settingsData = await bridge.apiPost("settings", collectSettings());
    state.maxChars = settingsData.values.max_note_chars;
    $("note-content").maxLength = state.maxChars;
    renderSettings();
    $("settings-hint").textContent = "已保存并立即生效。";
    notice("插件设置已保存。");
  } catch (error) {
    $("settings-error").textContent = error.message;
    $("settings-error").hidden = false;
  } finally {
    button.disabled = false;
  }
});

$("load-scope").addEventListener("click", () => action($("load-scope"), loadScope));
$("scope").addEventListener("keydown", (event) => {
  if (event.key === "Enter") { event.preventDefault(); action($("load-scope"), loadScope); }
});
$("reload-scopes").addEventListener("click", () => action($("reload-scopes"), loadScopes));
$("new-note").addEventListener("click", () => { try { openEditor(); } catch (error) { notice(error.message, true); } });
for (const button of document.querySelectorAll("[data-layer]")) {
  button.addEventListener("click", () => action(button, async () => { scopeRequired(); setLayer(button.dataset.layer); await refresh(); }));
}
$("search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(event.submitter || $("load-scope"), async () => {
    scopeRequired(); state.keyword = $("keyword").value.trim(); state.page = 1; await refresh();
  });
});
for (const [id, delta] of [["previous", -1], ["next", 1]]) {
  $(id).addEventListener("click", async () => {
    const old = state.page;
    state.page = Math.max(1, Math.min(state.pages, state.page + delta));
    try { await refresh(); } catch (error) { state.page = old; notice(error.message, true); }
  });
}
for (const id of ["close-editor", "cancel-editor"]) $(id).addEventListener("click", () => $("editor").close());
$("note-content").addEventListener("input", updateCharCount);
$("editor-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("save-note").disabled = true;
  $("editor-error").hidden = true;
  try {
    await bridge.apiPost("save", { scope: scopeRequired(), id: state.editing, layer: $("note-layer").value, content: $("note-content").value, tags: $("note-tags").value });
    $("editor").close();
    notice("便签已保存。现有会话如需立即使用新内容，请刷新会话快照。");
    await refresh();
  } catch (error) {
    $("editor-error").textContent = error.message;
    $("editor-error").hidden = false;
  } finally { $("save-note").disabled = false; }
});
$("cancel-delete").addEventListener("click", () => $("delete-dialog").close());
$("delete-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("confirm-delete").disabled = true;
  try {
    await bridge.apiPost("delete", { scope: scopeRequired(), id: state.deleting.id });
    $("delete-dialog").close();
    notice("便签已删除。");
    await refresh();
  } catch (error) { $("delete-error").textContent = error.message; $("delete-error").hidden = false; }
  finally { $("confirm-delete").disabled = false; }
});
$("summarize").addEventListener("click", () => action($("summarize"), async () => {
  notice("正在调用总结模型，请稍候…");
  const result = await bridge.apiPost("summary", { scope: scopeRequired() });
  notice(result.busy ? "该用户已有总结在运行。" : result.empty ? "没有待总结对话；只处理插件启用后完成的对话。" : `总结完成，新增 ${result.added} 条便签，待处理 ${result.pending_rounds} 轮。`);
  await refresh();
}));
$("refresh-memory").addEventListener("click", () => action($("refresh-memory"), async () => {
  await bridge.apiPost("refresh", { scope: scopeRequired() });
  notice("已标记刷新，该用户的所有会话将在下一次请求时装载最新便签。");
}));
$("export").addEventListener("click", () => action($("export"), async () => {
  await bridge.download("export", { scope: scopeRequired() }, "notememory-export.json");
  notice("已导出该用户的全部便签。");
}));

try {
  if (!bridge) throw new Error("请在 AstrBot WebUI 的插件详情 → Pages 中打开本页，不能直接打开 HTML 文件。");
  await bridge.ready();
  $("connection").textContent = "已连接 AstrBot";
  $("connection").classList.add("connected");
  await loadScopes();
  if ($("scope").value) await loadScope();
} catch (error) {
  $("connection").textContent = "连接未就绪";
  notice(error.message, true);
}
