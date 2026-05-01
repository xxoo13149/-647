import { createApp, escapeHtml } from "./nanofront.js";

const AUTO_REFRESH_MS = 2500;

const statusText = {
  queued: "排队中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
};

const initialForm = {
  name: "",
  platform: "zhaopin",
  keywords: "",
  regions: "",
  max_pages: "1",
  headless: true,
  max_empty_retries: "2",
  max_detail_retries: "1",
  detail_timeout_ms: "90000",
};

const initialState = {
  page: "settings",
  loading: true,
  refreshing: false,
  error: "",
  tasks: [],
  selectedTask: null,
  files: [],
  data: { columns: [], rows: [], file: "" },
  dataCollapsed: true,
  lastRefresh: "",
  defaults: { ...initialForm },
  form: { ...initialForm },
};

async function apiJson(path, options = {}) {
  const response = await fetch(path, {
    method: options.method ?? "GET",
    headers: { "Content-Type": "application/json" },
    body: options.payload ? JSON.stringify(options.payload) : undefined,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `请求失败：${response.status}`);
  }
  return data;
}

function nowText() {
  return new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

function taskIsActive(task) {
  return task && ["queued", "running"].includes(task.status);
}

function syncFormState(app) {
  const form = document.querySelector("[data-role='task-form']");
  if (!form) return;
  const data = new FormData(form);
  app.state.form = {
    name: data.get("name") || "",
    platform: data.get("platform") || "zhaopin",
    keywords: data.get("keywords") || "",
    regions: data.get("regions") || "",
    max_pages: data.get("max_pages") || "1",
    headless: data.has("headless"),
    max_empty_retries: data.get("max_empty_retries") || "2",
    max_detail_retries: data.get("max_detail_retries") || "1",
    detail_timeout_ms: data.get("detail_timeout_ms") || "90000",
  };
}

function taskCard(task, selectedTask, mode = "queue") {
  const selected = selectedTask && selectedTask.id === task.id;
  const meta = mode === "history"
    ? `${escapeHtml(task.created_at || "无创建时间")} · ${escapeHtml(task.platform)}`
    : `${escapeHtml(task.platform)} · ${escapeHtml(task.keywords.join(", "))}`;
  return `
    <article class="task-card${selected ? " selected" : ""}" data-action="select-task" data-id="${escapeHtml(task.id)}">
      <div class="task-line">
        <strong>${escapeHtml(task.name)}</strong>
        <span class="status ${escapeHtml(task.status)}">${escapeHtml(statusText[task.status] || task.status)}</span>
      </div>
      <div class="muted">${meta}</div>
      <div class="muted">地区：${escapeHtml(task.regions.join(", "))}</div>
      <div class="task-metrics">
        <span><b>${escapeHtml(task.raw_count)}</b><em>原始</em></span>
        <span><b>${escapeHtml(task.appended_count)}</b><em>新增</em></span>
        <span><b>${escapeHtml(task.updated_count)}</b><em>更新</em></span>
      </div>
    </article>
  `;
}

function renderLogs(task) {
  if (!task) return "";
  const logs = (task.logs || []).slice(-14).map((line) => `<div>${escapeHtml(line)}</div>`).join("");
  const error = task.error ? `<div class="log-error">${escapeHtml(task.error)}</div>` : "";
  return `
    <div class="log-box">
      <div class="log-title">任务日志</div>
      ${logs || `<div class="muted-on-dark">暂无日志</div>`}
      ${error}
    </div>
  `;
}

function renderFiles(files) {
  if (!files.length) {
    return `<span class="muted">暂无 Excel 文件</span>`;
  }
  return files.map((file) => `
    <div class="file-chip">
      <button type="button" data-action="load-file" data-file="${escapeHtml(file.name)}">${escapeHtml(file.name)}</button>
      <a href="${escapeHtml(file.download_url)}" target="_blank" rel="noreferrer">下载</a>
    </div>
  `).join("");
}

function renderTable(data) {
  const rows = data.rows || [];
  const columns = data.columns || [];
  if (!rows.length) {
    return `<div class="empty">当前文件暂无可预览数据。</div>`;
  }
  return `
    <div class="muted">预览文件：${escapeHtml(data.file)}（最多显示 100 行）</div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>${columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("")}</tr>
        </thead>
        <tbody>
          ${rows.map((row) => `
            <tr>${columns.map((column) => `<td>${escapeHtml(row[column] ?? "")}</td>`).join("")}</tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function navLink(page, currentPage, label) {
  return `<button class="nav-link${page === currentPage ? " active" : ""}" type="button" data-action="nav" data-page="${page}">${label}</button>`;
}

function shell(app, content) {
  const state = app.state;
  return `
    <div class="shell">
      <header class="topbar">
        <div>
          <div class="brand">招聘数据采集台</div>
          <div class="subtitle">创建任务、跟踪进度、预览和下载本地 Excel 结果</div>
        </div>
        <div class="top-actions">
          <nav class="nav-tabs">
            ${navLink("settings", state.page, "设置")}
            ${navLink("tasks", state.page, "新增任务")}
            ${navLink("history", state.page, "历史任务")}
          </nav>
          <button class="secondary" type="button" data-action="refresh" title="刷新任务状态">
            ${state.refreshing ? "刷新中..." : "刷新"}
          </button>
        </div>
      </header>

      ${state.error ? `<div class="alert">${escapeHtml(state.error)}</div>` : ""}
      ${state.loading ? `<div class="loading">正在加载默认配置和任务列表...</div>` : ""}
      ${content}
    </div>
  `;
}

function settingsPage(app) {
  const state = app.state;
  const defaults = state.defaults;
  const running = state.tasks.filter(taskIsActive).length;
  const completed = state.tasks.filter((task) => task.status === "completed").length;
  const failed = state.tasks.filter((task) => task.status === "failed").length;
  return shell(app, `
    <section class="panel settings-panel">
      <div class="panel-heading">
        <h2>设置总览</h2>
        <p>这里展示当前 .env 默认配置。需要创建采集任务时，请进入“新增任务”。</p>
      </div>
      <div class="summary-grid">
        <div><b>${escapeHtml(defaults.platform)}</b><span>默认平台</span></div>
        <div><b>${escapeHtml(defaults.max_pages)}</b><span>每组页数</span></div>
        <div><b>${escapeHtml(defaults.headless ? "是" : "否")}</b><span>无头浏览器</span></div>
        <div><b>${escapeHtml(defaults.detail_timeout_ms)}</b><span>详情超时 ms</span></div>
      </div>
      <div class="settings-list">
        <div><span>关键词</span><strong>${escapeHtml(defaults.keywords || "未配置")}</strong></div>
        <div><span>城市/地区</span><strong>${escapeHtml(defaults.regions || "未配置")}</strong></div>
        <div><span>空页重试</span><strong>${escapeHtml(defaults.max_empty_retries)}</strong></div>
        <div><span>详情重试</span><strong>${escapeHtml(defaults.max_detail_retries)}</strong></div>
      </div>
    </section>
    <section class="panel data-panel">
      <div class="panel-heading">
        <h2>任务概况</h2>
        <p>历史任务记录会在“历史任务”页保留，可点击查看详情。</p>
      </div>
      <div class="summary-grid">
        <div><b>${state.tasks.length}</b><span>全部任务</span></div>
        <div><b>${running}</b><span>进行中</span></div>
        <div><b>${completed}</b><span>已完成</span></div>
        <div><b>${failed}</b><span>失败</span></div>
      </div>
      ${state.lastRefresh ? `<div class="refresh-note">最近刷新：${escapeHtml(state.lastRefresh)}</div>` : ""}
    </section>
  `);
}

function taskForm(form) {
  return `
    <form data-role="task-form">
      <div class="field">
        <label for="name">任务名称</label>
        <input id="name" name="name" value="${escapeHtml(form.name)}" placeholder="可选，例如：上海 Java 岗" />
      </div>
      <div class="field grid-2">
        <div>
          <label for="platform">平台</label>
          <select id="platform" name="platform">
            <option value="zhaopin"${form.platform === "zhaopin" ? " selected" : ""}>智联招聘</option>
            <option value="51job"${form.platform === "51job" ? " selected" : ""}>51job</option>
          </select>
        </div>
        <div>
          <label for="max_pages">每组页数</label>
          <input id="max_pages" name="max_pages" type="number" min="1" value="${escapeHtml(form.max_pages)}" />
        </div>
      </div>
      <div class="field">
        <label for="keywords">关键词</label>
        <textarea id="keywords" name="keywords" placeholder="多个关键词用英文逗号分隔">${escapeHtml(form.keywords)}</textarea>
      </div>
      <div class="field">
        <label for="regions">城市/地区</label>
        <textarea id="regions" name="regions" placeholder="多个城市用英文逗号分隔">${escapeHtml(form.regions)}</textarea>
      </div>
      <div class="field grid-3">
        <div>
          <label for="max_empty_retries">空页重试</label>
          <input id="max_empty_retries" name="max_empty_retries" type="number" min="1" value="${escapeHtml(form.max_empty_retries)}" />
        </div>
        <div>
          <label for="max_detail_retries">详情重试</label>
          <input id="max_detail_retries" name="max_detail_retries" type="number" min="0" value="${escapeHtml(form.max_detail_retries)}" />
        </div>
        <div>
          <label for="detail_timeout_ms">详情超时 ms</label>
          <input id="detail_timeout_ms" name="detail_timeout_ms" type="number" min="5000" value="${escapeHtml(form.detail_timeout_ms)}" />
        </div>
      </div>
      <label class="check-row">
        <input name="headless" type="checkbox"${form.headless ? " checked" : ""} />
        <span>无头浏览器运行</span>
      </label>
      <button class="primary" type="submit">启动任务</button>
    </form>
  `;
}

function taskDetail(app, title = "任务详情") {
  const state = app.state;
  const task = state.selectedTask;
  if (!task) {
    return `
      <section class="panel data-panel">
        <div class="panel-heading">
          <h2>${title}</h2>
          <p>点击一个任务后，可以查看日志、文件和表格预览。</p>
        </div>
        <div class="empty">请选择一个任务。</div>
      </section>
    `;
  }
  const dataBody = state.dataCollapsed
    ? ""
    : `
      <div class="file-row">${renderFiles(state.files)}</div>
      ${renderTable(state.data)}
    `;
  return `
    <section class="panel data-panel ${state.dataCollapsed ? "is-collapsed" : ""}">
      <div class="panel-title-row">
        <div class="panel-heading">
          <h2>${title}</h2>
          <p>${escapeHtml(task.name)} · ${escapeHtml(task.created_at || "无创建时间")}</p>
        </div>
        <div class="panel-actions">
          <span class="pill">${escapeHtml(task.id)}</span>
          <button class="secondary compact" type="button" data-action="toggle-data">
            ${state.dataCollapsed ? "展开数据" : "折叠数据"}
          </button>
        </div>
      </div>
      <div class="detail-grid">
        <div><span>平台</span><strong>${escapeHtml(task.platform)}</strong></div>
        <div><span>关键词</span><strong>${escapeHtml(task.keywords.join(", "))}</strong></div>
        <div><span>地区</span><strong>${escapeHtml(task.regions.join(", "))}</strong></div>
        <div><span>状态</span><strong>${escapeHtml(statusText[task.status] || task.status)}</strong></div>
        <div><span>开始时间</span><strong>${escapeHtml(task.started_at || "-")}</strong></div>
        <div><span>结束时间</span><strong>${escapeHtml(task.finished_at || "-")}</strong></div>
      </div>
      <div class="collapsible-body">${dataBody}</div>
    </section>
  `;
}

function tasksPage(app) {
  const state = app.state;
  const runningTasks = state.tasks.filter(taskIsActive);
  const taskList = runningTasks.length
    ? runningTasks.map((task) => taskCard(task, state.selectedTask)).join("")
    : `<div class="empty">当前没有进行中的任务。创建一个任务后会在这里显示动态。</div>`;
  return shell(app, `
    <div class="layout">
      <section class="panel form-panel">
        <div class="panel-heading">
          <h2>新增任务</h2>
          <p>填写采集范围后即可启动，任务会在右侧实时更新。</p>
        </div>
        ${taskForm(state.form)}
      </section>
      <section class="panel task-panel">
        <div class="panel-title-row">
          <div class="panel-heading">
            <h2>任务队列</h2>
            <p>只显示排队中和运行中的任务。</p>
          </div>
          <span class="pill">${runningTasks.length} 个进行中</span>
        </div>
        <div class="task-list">${taskList}</div>
        ${renderLogs(state.selectedTask)}
        ${state.lastRefresh ? `<div class="refresh-note">最近刷新：${escapeHtml(state.lastRefresh)}</div>` : ""}
      </section>
    </div>
    ${taskDetail(app)}
  `);
}

function historyPage(app) {
  const state = app.state;
  const historyTasks = [...state.tasks].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  const taskList = historyTasks.length
    ? historyTasks.map((task) => taskCard(task, state.selectedTask, "history")).join("")
    : `<div class="empty">暂无历史任务。任务创建后会保存在这里。</div>`;
  return shell(app, `
    <div class="history-layout">
      <section class="panel task-panel">
        <div class="panel-title-row">
          <div class="panel-heading">
            <h2>历史任务</h2>
            <p>点击任务可以查看完整详情、日志和导出文件。</p>
          </div>
          <span class="pill">${historyTasks.length} 条记录</span>
        </div>
        <div class="task-list history-list">${taskList}</div>
        ${state.lastRefresh ? `<div class="refresh-note">最近刷新：${escapeHtml(state.lastRefresh)}</div>` : ""}
      </section>
      <div>
        ${renderLogs(state.selectedTask)}
      </div>
    </div>
    ${taskDetail(app, "历史任务详情")}
  `);
}

function dashboard(app) {
  if (app.state.page === "tasks") return tasksPage(app);
  if (app.state.page === "history") return historyPage(app);
  return settingsPage(app);
}

const app = createApp({
  element: "#app",
  initialState,
  view: dashboard,
  actions: {
    async bootstrap(app) {
      try {
        const defaults = await apiJson("/api/defaults");
        const normalized = {
          ...initialForm,
          ...Object.fromEntries(Object.entries(defaults).map(([key, value]) => [key, key === "headless" ? Boolean(value) : String(value)])),
          name: "",
        };
        app.state.defaults = normalized;
        app.state.form = { ...normalized };
        await app.actions.refresh({ silent: true });
      } catch (error) {
        app.setState({ error: error.message });
      } finally {
        app.setState({ loading: false });
      }
    },
    async nav(app, page) {
      syncFormState(app);
      app.state.page = page;
      app.state.error = "";
      if (page === "tasks") {
        const firstActive = app.state.tasks.find(taskIsActive);
        app.state.selectedTask = firstActive || null;
        app.state.dataCollapsed = true;
      }
      app.render();
    },
    async refresh(app, options = {}) {
      syncFormState(app);
      if (!options.silent) app.setState({ refreshing: true, error: "" });
      try {
        const tasks = await apiJson("/api/tasks");
        let selectedTask = app.state.selectedTask;
        if (selectedTask) {
          selectedTask = tasks.find((task) => task.id === selectedTask.id) || selectedTask;
        }
        app.state.tasks = tasks;
        app.state.selectedTask = selectedTask;
        app.state.lastRefresh = nowText();
        if (selectedTask && !app.state.dataCollapsed) {
          await app.actions.loadTaskAssets(selectedTask.id, { render: false, force: true });
        }
        app.render();
      } catch (error) {
        app.setState({ error: error.message });
      } finally {
        app.setState({ refreshing: false });
      }
    },
    async createTask(app, form) {
      syncFormState(app);
      const payload = { ...app.state.form };
      app.setState({ error: "" });
      try {
        const task = await apiJson("/api/tasks", { method: "POST", payload });
        app.state.selectedTask = task;
        app.state.dataCollapsed = true;
        app.state.form = { ...payload, name: "" };
        app.state.page = "tasks";
        await app.actions.refresh({ silent: true });
      } catch (error) {
        app.setState({ error: error.message });
      }
    },
    async selectTask(app, id) {
      syncFormState(app);
      const selectedTask = app.state.tasks.find((task) => task.id === id);
      if (!selectedTask) return;
      app.state.selectedTask = selectedTask;
      app.state.dataCollapsed = false;
      await app.actions.loadTaskAssets(id, { force: true });
    },
    async loadTaskAssets(app, id, options = {}) {
      if (app.state.dataCollapsed && !options.force) {
        if (options.render !== false) app.render();
        return;
      }
      const [files, data] = await Promise.all([
        apiJson(`/api/tasks/${id}/files`),
        apiJson(`/api/tasks/${id}/data`),
      ]);
      app.state.files = files;
      app.state.data = data;
      if (options.render !== false) app.render();
    },
    async loadFile(app, fileName) {
      if (!app.state.selectedTask) return;
      try {
        app.setState({ error: "" });
        const data = await apiJson(`/api/tasks/${app.state.selectedTask.id}/data?file=${encodeURIComponent(fileName)}`);
        app.setState({ data });
      } catch (error) {
        app.setState({ error: error.message });
      }
    },
    async toggleData(app) {
      syncFormState(app);
      const dataCollapsed = !app.state.dataCollapsed;
      app.state.dataCollapsed = dataCollapsed;
      if (!dataCollapsed && app.state.selectedTask) {
        await app.actions.loadTaskAssets(app.state.selectedTask.id, { force: true });
      } else {
        app.render();
      }
    },
  },
});

app.onRender((app) => {
  document.querySelector("[data-role='task-form']")?.addEventListener("submit", (event) => {
    event.preventDefault();
    app.actions.createTask(event.currentTarget);
  });

  document.querySelectorAll("[data-action='nav']").forEach((element) => {
    element.addEventListener("click", () => app.actions.nav(element.dataset.page));
  });

  document.querySelector("[data-action='refresh']")?.addEventListener("click", () => app.actions.refresh());
  document.querySelector("[data-action='toggle-data']")?.addEventListener("click", () => app.actions.toggleData());

  document.querySelectorAll("[data-action='select-task']").forEach((element) => {
    element.addEventListener("click", () => app.actions.selectTask(element.dataset.id));
  });

  document.querySelectorAll("[data-action='load-file']").forEach((element) => {
    element.addEventListener("click", () => app.actions.loadFile(element.dataset.file));
  });
}).mount();

app.actions.bootstrap();

setInterval(() => {
  if (document.hidden) return;
  const hasActiveTasks = app.state.tasks.some(taskIsActive);
  if (hasActiveTasks || app.state.selectedTask) {
    app.actions.refresh({ silent: true });
  }
}, AUTO_REFRESH_MS);
