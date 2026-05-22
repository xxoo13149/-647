import { createApp } from "./nanofront.js";
import { dashboard, taskIsActive } from "./views.js";

const AUTO_REFRESH_MS = 2500;
let saveDefaultsTimer = null;

const initialForm = {
  name: "",
  platform: "zhaopin",
  keywords: "java开发",
  regions: "北京,广州,上海",
  max_pages: "1",
  headless: true,
  skip_detail_fetch: false,
  refetch_crawled_details: false,
  filter_existing_output_early: false,
  max_empty_retries: "2",
  max_detail_retries: "1",
  detail_timeout_ms: "90000",
  delay_between_pages: "1.8,3.0",
};

const initialState = {
  page: "home",
  path: window.location.pathname,
  taskId: "",
  returnPage: "tasks",
  loading: true,
  refreshing: false,
  error: "",
  tasks: [],
  selectedTask: null,
  files: [],
  data: { columns: [], rows: [], file: "" },
  dataCollapsed: true,
  lastRefresh: "",
  historyPage: 1,
  historyPageSize: 8,
  defaults: { ...initialForm },
  form: { ...initialForm },
  auth51job: {
    status: "idle",
    profile_ready: false,
    user_data_dir: "",
    auth_wait_seconds: 120,
    logs: [],
    error: "",
  },
  authZhaopin: {
    status: "idle",
    profile_ready: false,
    user_data_dir: "",
    auth_wait_seconds: 120,
    logs: [],
    error: "",
  },
};

async function apiJson(path, options = {}) {
  const response = await fetch(path, {
    method: options.method ?? "GET",
    headers: { "Content-Type": "application/json" },
    body: options.payload ? JSON.stringify(options.payload) : undefined,
  });
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`接口 ${path} 返回了非 JSON 响应，请确认 Web 服务已重启并加载最新代码。`);
  }
  if (!response.ok) {
    throw new Error(data.error || `请求失败：${response.status}`);
  }
  return data;
}

function nowText() {
  return new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

function normalizeDefaults(defaults) {
  const normalized = { ...initialForm };
  for (const [key, value] of Object.entries(defaults || {})) {
    if (key === "headless") {
      normalized.headless = Boolean(value);
      continue;
    }
    if (key === "skip_detail_fetch") {
      normalized.skip_detail_fetch = Boolean(value);
      continue;
    }
    if (key === "refetch_crawled_details") {
      normalized.refetch_crawled_details = Boolean(value);
      continue;
    }
    if (key === "filter_existing_output_early") {
      normalized.filter_existing_output_early = Boolean(value);
      continue;
    }
    const text = String(value ?? "").trim();
    if (text) {
      normalized[key] = text;
    }
  }
  normalized.name = "";
  return normalized;
}

function parseRoute(pathname = window.location.pathname) {
  const path = pathname.replace(/\/$/, "") || "/";
  const detailMatch = path.match(/^\/(tasks|history)\/([^/]+)$/);
  if (detailMatch) {
    return {
      page: "detail",
      path,
      taskId: decodeURIComponent(detailMatch[2]),
      returnPage: detailMatch[1],
    };
  }
  if (path === "/tasks") return { page: "tasks", path, taskId: "", returnPage: "tasks" };
  if (path === "/history") return { page: "history", path, taskId: "", returnPage: "history" };
  return { page: "home", path: "/", taskId: "", returnPage: "tasks" };
}

function pathForPage(page) {
  if (page === "tasks") return "/tasks";
  if (page === "history") return "/history";
  return "/";
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
    skip_detail_fetch: data.get("skip_detail_fetch") !== "false",
    refetch_crawled_details: data.has("refetch_crawled_details"),
    filter_existing_output_early: data.has("filter_existing_output_early"),
    max_empty_retries: data.get("max_empty_retries") || "2",
    max_detail_retries: data.get("max_detail_retries") || "1",
    detail_timeout_ms: data.get("detail_timeout_ms") || "90000",
    delay_between_pages: data.get("delay_between_pages") || "1.8,3.0",
  };
}

function scheduleDefaultsSave(app) {
  syncFormState(app);
  if (saveDefaultsTimer) {
    clearTimeout(saveDefaultsTimer);
  }
  saveDefaultsTimer = setTimeout(async () => {
    try {
      const saved = await apiJson("/api/defaults", { method: "POST", payload: app.state.form });
      app.state.defaults = normalizeDefaults(saved);
    } catch {
      // Ignore background save errors to avoid interrupting typing.
    }
  }, 250);
}

function applyRoute(app, route = parseRoute()) {
  app.state.page = route.page;
  app.state.path = route.path;
  app.state.taskId = route.taskId;
  app.state.returnPage = route.returnPage || app.state.returnPage;
  if (route.page !== "detail") {
    app.state.selectedTask = null;
    app.state.dataCollapsed = true;
    return;
  }
  app.state.selectedTask = app.state.tasks.find((task) => task.id === route.taskId) || null;
}

function updateBrowserPath(path) {
  if (window.location.pathname === path) return;
  window.history.pushState({}, "", path);
}

async function navigate(app, path) {
  syncFormState(app);
  updateBrowserPath(path);
  applyRoute(app, parseRoute(path));
  app.state.error = "";
  app.render();
}

const app = createApp({
  element: "#app",
  initialState,
  view: dashboard,
  actions: {
    async bootstrap(app) {
      applyRoute(app);
      try {
        const defaults = await apiJson("/api/defaults");
        const normalized = normalizeDefaults(defaults);
        app.state.defaults = normalized;
        app.state.form = { ...normalized };
        const [auth51job, authZhaopin] = await Promise.all([
          apiJson("/api/51job/auth"),
          apiJson("/api/zhaopin/auth"),
        ]);
        app.state.auth51job = auth51job;
        app.state.authZhaopin = authZhaopin;
        await app.actions.refresh({ silent: true });
      } catch (error) {
        app.setState({ error: error.message });
      } finally {
        app.setState({ loading: false });
      }
    },
    async navigate(app, path) {
      await navigate(app, path);
    },
    async refresh(app, options = {}) {
      syncFormState(app);
      if (!options.silent) app.setState({ refreshing: true, error: "" });
      try {
        const [tasks, auth51job, authZhaopin] = await Promise.all([
          apiJson("/api/tasks"),
          apiJson("/api/51job/auth"),
          apiJson("/api/zhaopin/auth"),
        ]);
        app.state.tasks = tasks;
        app.state.auth51job = auth51job;
        app.state.authZhaopin = authZhaopin;
        applyRoute(app, parseRoute(app.state.path));
        app.state.lastRefresh = nowText();
        if (app.state.selectedTask && app.state.page === "detail" && !app.state.dataCollapsed) {
          await app.actions.loadTaskAssets(app.state.selectedTask.id, { render: false, force: true });
        }
        app.render();
      } catch (error) {
        app.setState({ error: error.message });
      } finally {
        app.setState({ refreshing: false });
      }
    },
    async createTask(app) {
      syncFormState(app);
      const payload = { ...app.state.form };
      app.setState({ error: "" });
      try {
        const savedDefaults = await apiJson("/api/defaults", { method: "POST", payload });
        app.state.defaults = normalizeDefaults(savedDefaults);
        const task = await apiJson("/api/tasks", { method: "POST", payload });
        app.state.selectedTask = task;
        app.state.dataCollapsed = true;
        app.state.form = { ...payload, name: "" };
        await app.actions.refresh({ silent: true });
        await navigate(app, `/tasks/${encodeURIComponent(task.id)}`);
      } catch (error) {
        app.setState({ error: error.message });
      }
    },
    async start51jobLogin(app) {
      try {
        app.setState({ error: "" });
        const force = app.state.auth51job?.status === "running" ? "?force=1" : "";
        app.state.auth51job = await apiJson(`/api/51job/auth${force}`, { method: "POST" });
        app.render();
      } catch (error) {
        app.setState({ error: error.message });
      }
    },
    async startZhaopinLogin(app) {
      try {
        app.setState({ error: "" });
        const force = app.state.authZhaopin?.status === "running" ? "?force=1" : "";
        app.state.authZhaopin = await apiJson(`/api/zhaopin/auth${force}`, { method: "POST" });
        app.render();
      } catch (error) {
        app.setState({ error: error.message });
      }
    },
    async cancelTask(app, id = "") {
      const taskId = id || app.state.selectedTask?.id;
      if (!taskId) return;
      if (!window.confirm("确定中止这个任务吗？已写入的数据和日志会保留。")) return;
      try {
        app.setState({ error: "" });
        const updatedTask = await apiJson(`/api/tasks/${taskId}/cancel`, { method: "POST" });
        if (app.state.selectedTask?.id === updatedTask.id) {
          app.state.selectedTask = updatedTask;
        }
        await app.actions.refresh({ silent: true });
      } catch (error) {
        app.setState({ error: error.message });
      }
    },
    async resetDefaults(app) {
      try {
        app.setState({ error: "" });
        const defaults = await apiJson("/api/defaults/reset", { method: "POST" });
        const normalized = normalizeDefaults(defaults);
        app.state.defaults = normalized;
        app.state.form = { ...normalized };
        app.render();
      } catch (error) {
        app.setState({ error: error.message });
      }
    },
    async openDetail(app, id) {
      const sourcePage = app.state.page === "history" ? "history" : "tasks";
      await navigate(app, `/${sourcePage}/${encodeURIComponent(id)}`);
    },
    changeHistoryPage(app, page) {
      const nextPage = Number(page);
      if (!Number.isFinite(nextPage) || nextPage < 1) return;
      app.state.historyPage = nextPage;
      app.render();
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
    async back(app) {
      await navigate(app, pathForPage(app.state.returnPage || "tasks"));
    },
  },
});

app.onRender((app) => {
  document.querySelector("[data-role='task-form']")?.addEventListener("submit", (event) => {
    event.preventDefault();
    app.actions.createTask();
  });

  document.querySelector("[data-role='task-form']")?.addEventListener("input", () => {
    scheduleDefaultsSave(app);
  });

  document.querySelector("[data-role='task-form']")?.addEventListener("change", () => {
    scheduleDefaultsSave(app);
  });

  document.querySelectorAll("[data-route]").forEach((element) => {
    element.addEventListener("click", (event) => {
      event.preventDefault();
      app.actions.navigate(element.getAttribute("href"));
    });
  });

  document.querySelector("[data-action='refresh']")?.addEventListener("click", () => app.actions.refresh());
  document.querySelector("[data-action='reset-defaults']")?.addEventListener("click", () => app.actions.resetDefaults());
  document.querySelector("[data-action='start-51job-login']")?.addEventListener("click", () => app.actions.start51jobLogin());
  document.querySelector("[data-action='start-zhaopin-login']")?.addEventListener("click", () => app.actions.startZhaopinLogin());
  document.querySelector("[data-action='toggle-data']")?.addEventListener("click", () => app.actions.toggleData());
  document.querySelectorAll("[data-action='cancel-task']").forEach((element) => {
    element.addEventListener("click", (event) => {
      event.stopPropagation();
      app.actions.cancelTask(element.dataset.id || "");
    });
  });
  document.querySelector("[data-action='back']")?.addEventListener("click", () => app.actions.back());

  document.querySelectorAll("[data-action='history-page']").forEach((element) => {
    element.addEventListener("click", () => app.actions.changeHistoryPage(element.dataset.page));
  });

  document.querySelectorAll("[data-action='open-detail']").forEach((element) => {
    element.addEventListener("click", (event) => {
      if (event.target.closest("[data-action='cancel-task']")) return;
      app.actions.openDetail(element.dataset.id);
    });
  });

  document.querySelectorAll("[data-action='load-file']").forEach((element) => {
    element.addEventListener("click", () => app.actions.loadFile(element.dataset.file));
  });
}).mount();

window.addEventListener("popstate", () => {
  applyRoute(app);
  app.render();
});

app.actions.bootstrap();

setInterval(() => {
  if (document.hidden) return;
  const hasActiveTasks = app.state.tasks.some(taskIsActive);
  const detailTaskIsActive = app.state.page === "detail" && taskIsActive(app.state.selectedTask);
  const authIsActive = ["running", "queued"].includes(app.state.auth51job?.status)
    || ["running", "queued"].includes(app.state.authZhaopin?.status);
  if (hasActiveTasks || detailTaskIsActive || authIsActive) {
    app.actions.refresh({ silent: true });
  }
}, AUTO_REFRESH_MS);
