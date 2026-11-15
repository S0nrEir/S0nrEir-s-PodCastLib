/* 播客选题库 —— 前端逻辑（无依赖） */
"use strict";

const $ = (sel, el) => (el || document).querySelector(sel);
const $$ = (sel, el) => Array.from((el || document).querySelectorAll(sel));
const app = $("#app");

let S = {
  view: "loading",        // loading | init | login | list | detail
  topics: [],
  settings: { sort_field: "updated_at", sort_order: "desc" },
  search: "",
  currentId: null,
};

const CATS = [
  { key: "like", label: "我喜欢它什么", emoji: "👍", cls: "like" },
  { key: "dislike", label: "我不喜欢它什么", emoji: "👎", cls: "dislike" },
  { key: "thought", label: "它让我想到什么", emoji: "💭", cls: "thought" },
];

/* ---------------- 工具 ---------------- */

async function api(path, opts = {}) {
  const cfg = {
    method: opts.method || "GET",
    headers: {},
    credentials: "same-origin",
  };
  if (opts.body !== undefined) {
    cfg.headers["Content-Type"] = "application/json";
    cfg.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, cfg);
  let data = null;
  try { data = await res.json(); } catch (_) { /* 空响应 */ }
  if (res.status === 401 && !opts.noCatch401) {
    S.view = "login";
    render();
    throw new Error((data && data.error) || "请先登录");
  }
  if (!res.ok) throw new Error((data && data.error) || `请求失败 (${res.status})`);
  return data;
}

function esc(s) {
  return String(s == null ? "" : s);
}

/* HTML 转义，用于插入模板字符串的用户内容 */
function h(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function toast(msg, isErr) {
  const el = document.createElement("div");
  el.className = "toast" + (isErr ? " err" : "");
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; }, 2200);
  setTimeout(() => el.remove(), 2600);
}

function relTime(ts) {
  if (!ts) return "";
  const diff = Math.floor(Date.now() / 1000) - ts;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return Math.floor(diff / 60) + " 分钟前";
  if (diff < 86400) return Math.floor(diff / 3600) + " 小时前";
  if (diff < 86400 * 7) return Math.floor(diff / 86400) + " 天前";
  const d = new Date(ts * 1000);
  const p = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function showConfirm(msg, okText) {
  return new Promise(resolve => {
    const dlg = $("#confirmDlg");
    $("#confirmMsg").textContent = msg;
    const yes = $("#confirmYes"), no = $("#confirmNo");
    yes.textContent = okText || "确定";
    let settled = false;
    const done = val => {
      if (settled) return;
      settled = true;
      try { dlg.close(); } catch (_) {}
      yes.onclick = no.onclick = dlg.onclose = null;
      resolve(val);
    };
    yes.onclick = () => done(true);
    no.onclick = () => done(false);
    dlg.onclose = () => done(false);
    dlg.showModal();
  });
}

function showPrompt(msg, defval, okText) {
  return new Promise(resolve => {
    const dlg = $("#promptDlg");
    $("#promptMsg").textContent = msg;
    const input = $("#promptInput");
    input.value = defval || "";
    const yes = $("#promptYes"), no = $("#promptNo");
    yes.textContent = okText || "确定";
    const grow = () => {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 220) + "px";
    };
    let settled = false;
    const done = val => {
      if (settled) return;
      settled = true;
      try { dlg.close(); } catch (_) {}
      yes.onclick = no.onclick = dlg.onclose = input.onkeydown = input.oninput = null;
      resolve(val);
    };
    yes.onclick = () => done(input.value.trim());
    no.onclick = () => done(null);
    dlg.onclose = () => done(null);
    input.oninput = grow;
    input.onkeydown = e => {
      // 输入法安全：组词中的 Enter（isComposing / keyCode 229）只用于确认候选词，不触发提交
      if (e.key === "Enter" && !e.isComposing && e.keyCode !== 229) {
        e.preventDefault();
        const v = input.value.trim();
        // 延迟到组合事件结束后再关闭弹窗，规避 IME 下 close() 的假死残留
        setTimeout(() => done(v), 0);
      }
    };
    dlg.showModal();
    setTimeout(() => { input.focus(); grow(); }, 50);
  });
}

/* ---------------- 渲染 ---------------- */

function render() {
  if (S.view === "loading") { app.innerHTML = '<div class="empty"><div class="big">⏳</div>加载中…</div>'; return; }
  if (S.view === "init") return renderInit();
  if (S.view === "login") return renderLogin();
  if (S.view === "detail") return renderDetail();
  renderList();
}

function renderInit() {
  app.innerHTML = `
  <div class="auth-wrap"><div class="auth-card">
    <div class="emoji">🎙️</div>
    <h1>欢迎使用播客选题库</h1>
    <p>首次使用，请设置一个访问密码<br>（只有你自己会访问，但多一道锁更安心）</p>
    <div class="form-col">
      <input type="password" id="pw1" placeholder="设置密码（至少 6 位）" autocomplete="new-password">
      <input type="password" id="pw2" placeholder="再输入一次" autocomplete="new-password">
      <button class="btn primary" id="initBtn">保存并进入</button>
    </div>
  </div></div>`;
  const go = async () => {
    const p1 = $("#pw1").value, p2 = $("#pw2").value;
    if (p1.length < 6) return toast("密码至少 6 位", true);
    if (p1 !== p2) return toast("两次输入不一致", true);
    try {
      await api("/api/setup", { method: "POST", body: { password: p1 } });
      toast("密码已设置");
      await enterApp();
    } catch (e) { toast(e.message, true); }
  };
  $("#initBtn").onclick = go;
  $("#pw2").onkeydown = e => { if (e.key === "Enter") go(); };
  $("#pw1").focus();
}

function renderLogin() {
  app.innerHTML = `
  <div class="auth-wrap"><div class="auth-card">
    <div class="emoji">🔒</div>
    <h1>播客选题库</h1>
    <p>请输入访问密码</p>
    <div class="form-col">
      <input type="password" id="pw" placeholder="访问密码" autocomplete="current-password">
      <button class="btn primary" id="loginBtn">进入</button>
    </div>
  </div></div>`;
  const go = async () => {
    try {
      await api("/api/login", { method: "POST", body: { password: $("#pw").value } });
      await enterApp();
    } catch (e) { toast(e.message, true); }
  };
  $("#loginBtn").onclick = go;
  $("#pw").onkeydown = e => { if (e.key === "Enter") go(); };
  $("#pw").focus();
}

async function enterApp() {
  const st = await api("/api/state");
  S.settings = st.settings || S.settings;
  await reloadTopics();
  S.view = "list";
  location.hash = "";
  render();
}

async function reloadTopics() {
  const data = await api("/api/topics");
  S.topics = data.topics;
}

function currentTopic() {
  return S.topics.find(t => t.id === S.currentId) || null;
}

function topbarHtml(sub) {
  return `
  <div class="topbar">
    <div class="logo">🎙️ <span class="logo-text">播客选题库</span></div>
    <div class="search"><input type="text" id="searchBox" placeholder="搜索标题 / 备注 / 内容…" value="${h(S.search)}"></div>
    ${sub || ""}
    <button class="btn primary new-topic" id="newBtn" title="新建选题">＋<span class="btn-label">新建选题</span></button>
    <button class="btn ghost" id="settingsBtn" title="设置">⚙<span class="btn-label">设置</span></button>
  </div>`;
}

function bindTopbar() {
  $("#newBtn").onclick = newTopic;
  $("#settingsBtn").onclick = openSettings;
  const box = $("#searchBox");
  box.oninput = () => {
    S.search = box.value.trim().toLowerCase();
    renderTopicGrid();
  };
}

function filteredTopics() {
  if (!S.search) return S.topics;
  const kw = S.search;
  return S.topics.filter(t =>
    t.title.toLowerCase().includes(kw) ||
    (t.notes || "").toLowerCase().includes(kw) ||
    t.items.some(it => it.content.toLowerCase().includes(kw))
  );
}

function renderList() {
  app.innerHTML = topbarHtml(`<span class="count-badge" id="countBadge"></span>`) +
    `<div class="main"><div class="topic-grid" id="topicGrid"></div></div>`;
  bindTopbar();
  renderTopicGrid();
}

function renderTopicGrid() {
  const grid = $("#topicGrid");
  const badge = $("#countBadge");
  const list = filteredTopics();
  if (badge) {
    badge.textContent = S.search
      ? `${list.length}/${S.topics.length} 条`
      : `共 ${S.topics.length} 条`;
  }
  if (!S.topics.length) {
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1"><div class="big">🎙️</div>
      还没有选题<br>点右上角「＋ 新建选题」记录你的第一个灵感吧</div>`;
    return;
  }
  if (!list.length) {
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1"><div class="big">🔍</div>没有匹配的选题</div>`;
    return;
  }
  grid.innerHTML = "";
  for (const t of list) {
    const card = document.createElement("div");
    card.className = "topic-card";
    const counts = { like: 0, dislike: 0, thought: 0 };
    t.items.forEach(it => counts[it.category]++);
    card.innerHTML = `
      <h3></h3>
      <div class="notes"></div>
      <div class="chips">
        ${counts.like ? `<span class="chip like">👍 ${counts.like}</span>` : ""}
        ${counts.dislike ? `<span class="chip dislike">👎 ${counts.dislike}</span>` : ""}
        ${counts.thought ? `<span class="chip thought">💭 ${counts.thought}</span>` : ""}
        ${!t.items.length ? `<span class="chip" style="color:var(--muted);border-color:var(--border)">还没有内容</span>` : ""}
      </div>
      <div class="time">更新于 ${relTime(t.updated_at)}</div>`;
    card.querySelector("h3").textContent = t.title;
    card.querySelector(".notes").textContent = t.notes || "";
    card.onclick = () => { location.hash = "#t" + t.id; };
    card.oncontextmenu = e => { e.preventDefault(); deleteTopicFlow(t); };
    grid.appendChild(card);
  }
}

async function newTopic() {
  const title = await showPrompt("新选题的标题叫什么？", "", "创建");
  if (!title) return;
  try {
    const data = await api("/api/topics", { method: "POST", body: { title } });
    await reloadTopics();
    S.search = "";
    location.hash = "#t" + data.topic.id;
    // 兜底：确保弹窗在任何浏览器/输入法状态下都已关闭
    const dlg = $("#promptDlg");
    if (dlg && dlg.open) { try { dlg.close(); } catch (_) {} }
    toast("选题已创建");
  } catch (e) { toast(e.message, true); }
}

async function deleteTopicFlow(t) {
  const ok = await showConfirm(`删除选题「${t.title}」？\n其下所有内容（喜欢/不喜欢/联想）都会一起删除，且无法恢复。`, "删除");
  if (!ok) return;
  try {
    await api("/api/topics/" + t.id, { method: "DELETE" });
    await reloadTopics();
    if (S.view === "detail" && S.currentId === t.id) { location.hash = ""; }
    else renderTopicGrid();
    toast("已删除");
  } catch (e) { toast(e.message, true); }
}

/* ---------------- 详情页 ---------------- */

function renderDetail() {
  const t = currentTopic();
  if (!t) { location.hash = ""; return; }
  app.innerHTML = `
  <div class="topbar">
    <div class="logo">🎙️ <span class="logo-text">播客选题库</span></div>
    <div class="spacer"></div>
    <button class="btn danger" id="delTopicBtn" title="删除本选题">🗑<span class="btn-label">删除本选题</span></button>
    <button class="btn ghost" id="settingsBtn">⚙<span class="btn-label">设置</span></button>
  </div>
  <div class="detail">
    <a href="#" class="back-link" id="backBtn">← 返回列表</a>
    <textarea class="detail-title" id="titleBox" rows="1" placeholder="选题标题"></textarea>
    <div class="detail-meta" id="metaLine"></div>
    <textarea class="detail-notes" id="notesBox" placeholder="补充备注（可留空）：背景、参考资料、为什么想做这期…"></textarea>
    <div class="sections"></div>
  </div>`;
  $("#delTopicBtn").onclick = () => deleteTopicFlow(t);
  $("#settingsBtn").onclick = openSettings;
  $("#backBtn").onclick = e => { e.preventDefault(); location.hash = ""; };

  const titleBox = $("#titleBox");
  titleBox.value = t.title;
  const notesBox = $("#notesBox");
  notesBox.value = t.notes || "";
  const meta = $("#metaLine");
  meta.textContent = `创建于 ${relTime(t.created_at)} · 更新于 ${relTime(t.updated_at)}`;

  const autoGrow = el => { el.style.height = "auto"; el.style.height = el.scrollHeight + "px"; };
  autoGrow(titleBox);

  titleBox.oninput = () => autoGrow(titleBox);
  titleBox.onblur = () => saveField("title", titleBox.value.trim());
  notesBox.onblur = () => saveField("notes", notesBox.value.trim());
  titleBox.onkeydown = e => {
    if (e.key === "Enter" && !e.isComposing && e.keyCode !== 229) {
      e.preventDefault();
      titleBox.blur();
    }
  };

  async function saveField(field, value) {
    const t2 = currentTopic();
    if (!t2) return;
    if ((field === "title" ? t2.title : t2.notes || "") === value) return;
    if (field === "title" && !value) { titleBox.value = t2.title; return toast("标题不能为空", true); }
    try {
      await api("/api/topics/" + t2.id, { method: "PUT", body: { [field]: value } });
      if (field === "title") t2.title = value; else t2.notes = value;
      t2.updated_at = Math.floor(Date.now() / 1000);
      toast("已保存");
    } catch (e) { toast(e.message, true); }
  }

  const wrap = $(".sections");
  for (const cat of CATS) {
    wrap.appendChild(buildSection(t, cat));
  }
}

function buildSection(t, cat) {
  const sec = document.createElement("div");
  sec.className = "section";
  sec.innerHTML = `<h3><span class="dot" style="background:var(--${cat.cls})"></span>${cat.emoji} ${cat.label}<span class="cnt"></span></h3>
    <div class="item-list"></div>
    <div class="add-row">
      <input type="text" placeholder="添加一条…（回车确认）" maxlength="2000">
      <button class="btn small primary">添加</button>
    </div>`;
  const list = $(".item-list", sec);
  const input = $(".add-row input", sec);
  const addBtn = $(".add-row button", sec);

  const refresh = () => {
    const t2 = currentTopic();
    const items = t2 ? t2.items.filter(i => i.category === cat.key) : [];
    $(".cnt", sec).textContent = items.length ? String(items.length) : "";
    list.innerHTML = "";
    if (!items.length) {
      list.innerHTML = `<div class="section-empty">还没有内容，在下面添加第一条吧</div>`;
    }
    for (const it of items) list.appendChild(buildItemRow(it, refresh));
  };

  const doAdd = async () => {
    const content = input.value.trim();
    if (!content) return;
    try {
      await api(`/api/topics/${t.id}/items`, { method: "POST", body: { category: cat.key, content } });
      await refreshTopic(t.id);
      input.value = "";
      refresh();
    } catch (e) { toast(e.message, true); }
  };
  addBtn.onclick = doAdd;
  input.onkeydown = e => {
    if (e.key === "Enter" && !e.isComposing && e.keyCode !== 229) {
      e.preventDefault();
      doAdd();
    }
  };

  refresh();
  sec._refresh = refresh;
  return sec;
}

function buildItemRow(it, refresh) {
  const row = document.createElement("div");
  row.className = "item";
  row.innerHTML = `
    <div class="text"></div>
    <div class="ops">
      <button class="icon-btn edit" title="编辑">✏️</button>
      <button class="icon-btn del" title="删除">✕</button>
    </div>`;
  $(".text", row).textContent = it.content;

  $(".edit", row).onclick = () => {
    row.innerHTML = `
      <div class="item-edit">
        <input type="text" maxlength="2000">
        <button class="icon-btn" title="保存" style="color:var(--like)">✓</button>
        <button class="icon-btn" title="取消">✕</button>
      </div>`;
    const input = $("input", row);
    input.value = it.content;
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
    const save = async () => {
      const v = input.value.trim();
      if (!v) return toast("内容不能为空", true);
      if (v === it.content) return refresh();
      try {
        await api("/api/items/" + it.id, { method: "PUT", body: { content: v } });
        await refreshTopic(it.topic_id);
        refresh();
      } catch (e) { toast(e.message, true); }
    };
    $("button[title=保存]", row).onclick = save;
    $("button[title=取消]", row).onclick = refresh;
    input.onkeydown = e => {
      if (e.key === "Enter" && !e.isComposing && e.keyCode !== 229) {
        e.preventDefault();
        save();
      }
      if (e.key === "Escape") refresh();
    };
  };

  $(".del", row).onclick = async () => {
    const ok = await showConfirm("删除这条内容？", "删除");
    if (!ok) return;
    try {
      await api("/api/items/" + it.id, { method: "DELETE" });
      await refreshTopic(it.topic_id);
      refresh();
    } catch (e) { toast(e.message, true); }
  };
  return row;
}

async function refreshTopic(id) {
  const data = await api("/api/topics");
  S.topics = data.topics;
  const t = currentTopic();
  if (t) {
    const meta = $("#metaLine");
    if (meta) meta.textContent = `创建于 ${relTime(t.created_at)} · 更新于 ${relTime(t.updated_at)}`;
  }
}

/* ---------------- 设置 ---------------- */

function openSettings() {
  const dlg = $("#settingsDlg");
  $("#sortField").value = S.settings.sort_field;
  $("#sortOrder").value = S.settings.sort_order;
  $("#oldPw").value = $("#newPw").value = $("#newPw2").value = "";
  $("#importFile").value = "";
  $("#importBtn").disabled = true;
  $$(".file-label").forEach(el => el.classList.remove("picked"));

  const saveSort = async () => {
    try {
      const data = await api("/api/settings", {
        method: "PUT",
        body: { sort_field: $("#sortField").value, sort_order: $("#sortOrder").value },
      });
      S.settings = data.settings;
      await reloadTopics();
      if (S.view === "list") renderTopicGrid();
      toast("排序已更新");
    } catch (e) { toast(e.message, true); }
  };
  $("#sortField").onchange = saveSort;
  $("#sortOrder").onchange = saveSort;

  $("#pwBtn").onclick = async () => {
    const old_pw = $("#oldPw").value, n1 = $("#newPw").value, n2 = $("#newPw2").value;
    if (n1.length < 6) return toast("新密码至少 6 位", true);
    if (n1 !== n2) return toast("两次新密码不一致", true);
    try {
      await api("/api/password", { method: "POST", body: { old_password: old_pw, new_password: n1 } });
      $("#oldPw").value = $("#newPw").value = $("#newPw2").value = "";
      toast("密码已修改");
    } catch (e) { toast(e.message, true); }
  };

  const fileInput = $("#importFile");
  const fileLabel = $(".file-label");
  const fileLabelText = fileLabel.firstChild.textContent;
  fileInput.onchange = () => {
    $("#importBtn").disabled = !fileInput.files.length;
    fileLabel.classList.toggle("picked", !!fileInput.files.length);
    fileLabel.firstChild.textContent = fileInput.files.length
      ? `已选择：${fileInput.files[0].name} `
      : fileLabelText;
  };
  $("#importBtn").onclick = async () => {
    const mode = $$('input[name=importMode]').find(r => r.checked).value;
    if (!fileInput.files.length) return;
    const file = fileInput.files[0];
    if (file.size > 4 * 1024 * 1024) return toast("文件太大（>4MB）", true);
    const msg = mode === "replace"
      ? "替换导入会先清空当前所有选题再导入，确定继续？"
      : `将把文件中的记录追加导入（不会影响现有记录），确定继续？`;
    if (!await showConfirm(msg, "导入")) return;
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      const r = await api("/api/import", { method: "POST", body: { data, mode } });
      toast(`导入成功：${r.topics} 条选题、${r.items} 条内容`);
      await reloadTopics();
      if (S.view === "list") renderTopicGrid();
      dlg.close();
    } catch (e) { toast("导入失败：" + e.message, true); }
  };

  $("#logoutBtn").onclick = async () => {
    await api("/api/logout", { method: "POST" });
    S.view = "login";
    dlg.close();
    render();
  };
  $("#settingsClose").onclick = () => dlg.close();
  dlg.showModal();
}

/* ---------------- 路由 & 启动 ---------------- */

function route() {
  const m = location.hash.match(/^#t(\d+)$/);
  if (m) {
    S.currentId = parseInt(m[1], 10);
    if (!currentTopic()) { location.hash = ""; return; }
    S.view = "detail";
  } else if (["list", "detail"].includes(S.view)) {
    S.view = "list";
  }
  render();
}

window.addEventListener("hashchange", () => {
  const m = location.hash.match(/^#t(\d+)$/);
  if (m) {
    S.currentId = parseInt(m[1], 10);
    if (!currentTopic()) { location.hash = ""; return; }
    S.view = "detail";
    render();
  } else {
    S.view = "list";
    render();
  }
});

(async function start() {
  try {
    const st = await api("/api/state", { noCatch401: true });
    if (!st.initialized) { S.view = "init"; return render(); }
    if (!st.authed) { S.view = "login"; return render(); }
    S.settings = st.settings || S.settings;
    await reloadTopics();
    S.view = "list";
    render();
    route();
  } catch (e) {
    app.innerHTML = `<div class="empty"><div class="big">⚠️</div>无法连接服务器<br>${esc(e.message)}</div>`;
  }
})();
