import { escapeHtml } from "./nanofront.js";

export const statusText = {
  queued: "排队中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
};

statusText.stopping = "中止中";
statusText.stopped = "已中止";

export function taskIsActive(task) {
  return task && ["queued", "running", "stopping"].includes(task.status);
}

function taskCanCancel(task) {
  return task && ["queued", "running"].includes(task.status);
}

function formatRegions(regions) {
  return (regions || []).length ? regions.join(", ") : "不限地区";
}

function platformText(platform) {
  if (platform === "zhaopin") return "智联招聘";
  if (platform === "51job") return "51job";
  return platform || "-";
}

function navLink(path, currentPath, label) {
  const normalizedPath = currentPath.replace(/\/$/, "") || "/";
  const isActive = path === "/"
    ? normalizedPath === "/"
    : normalizedPath === path || normalizedPath.startsWith(`${path}/`);
  return `<a class="nav-link${isActive ? " active" : ""}" href="${path}" data-route>${label}</a>`;
}

function shell(app, content) {
  const state = app.state;
  return `
    <div class="shell">
      <header class="topbar">
        <div>
          <div class="brand">招聘数据采集台</div>
          <div class="subtitle">配置参数、创建任务、查看进度和下载 Excel 结果</div>
        </div>
        <div class="top-actions">
          <nav class="nav-tabs">
            ${navLink("/", state.path, "首页")}
            ${navLink("/tasks", state.path, "进行中")}
            ${navLink("/history", state.path, "历史任务")}
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

function taskCard(task, selectedTask, mode = "queue") {
  const selected = selectedTask && selectedTask.id === task.id;
  const meta = mode === "history"
    ? `${escapeHtml(task.created_at || "无创建时间")} · ${escapeHtml(task.platform)}`
    : `${escapeHtml(task.platform)} · ${escapeHtml(task.keywords.join(", "))}`;
  return `
    <article class="task-card${selected ? " selected" : ""}" data-action="open-detail" data-id="${escapeHtml(task.id)}">
      <div class="task-line">
        <strong>${escapeHtml(task.name)}</strong>
        <span class="status ${escapeHtml(task.status)}">${escapeHtml(statusText[task.status] || task.status)}</span>
        ${taskCanCancel(task) ? `<button class="secondary compact danger-action" type="button" data-action="cancel-task" data-id="${escapeHtml(task.id)}">中止</button>` : ""}
      </div>
      <div class="muted">${meta}</div>
      <div class="muted">地区：${escapeHtml(formatRegions(task.regions))}</div>
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
  const logs = (task.logs || []).slice(0, 120).map((line) => `<div>${escapeHtml(line)}</div>`).join("");
  const error = task.error ? `<div class="log-error">${escapeHtml(task.error)}</div>` : "";
  return `
    <div class="log-box detail-log">
      <div class="log-title">运行日志</div>
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

function renderAuthCard(auth, options = {}) {
  const state = auth || {};
  const platformName = options.platformName || "51job";
  const action = options.action || "start-51job-login";
  const status = state.status || "idle";
  const statusLabel = {
    idle: "\u672a\u542f\u52a8",
    queued: "\u6392\u961f\u4e2d",
    running: "\u767b\u5f55\u4e2d",
    completed: "\u5df2\u5b8c\u6210",
    failed: "\u5931\u8d25",
  }[status] || status;
  const readyLabel = state.profile_ready ? "\u5df2\u4fdd\u5b58" : "\u672a\u5c31\u7eea";
  const logs = (state.logs || []).slice(0, 6).map((line) => `<div>${escapeHtml(line)}</div>`).join("");
  const running = status === "queued" || status === "running";
  const error = state.error ? `<div class="log-error auth-error">${escapeHtml(state.error)}</div>` : "";
  return `
    <div class="auth-card">
      <div class="auth-head">
        <div>
          <strong>${escapeHtml(platformName)} \u767b\u5f55</strong>
          <span>${escapeHtml(readyLabel)} · ${escapeHtml(statusLabel)}</span>
        </div>
        <button class="secondary compact" type="button" data-action="${escapeHtml(action)}">
          ${running ? "\u91cd\u65b0\u6253\u5f00\u767b\u5f55" : "\u6253\u5f00\u767b\u5f55"}
        </button>
      </div>
      <div class="muted">Profile: ${escapeHtml(state.user_data_dir || "-")}</div>
      <div class="muted">\u767b\u5f55\u7a97\u53e3\u9ed8\u8ba4\u7b49\u5f85 ${escapeHtml(state.auth_wait_seconds || 120)} \u79d2\uff0c\u5b8c\u6210\u767b\u5f55/\u9a8c\u8bc1\u540e\u4f1a\u81ea\u52a8\u4fdd\u5b58\u4f1a\u8bdd\u3002</div>
      ${error}
      ${logs ? `<div class="auth-log">${logs}</div>` : ""}
    </div>
  `;
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

function taskForm(form, submitText = "新增任务") {
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
        <label for="skip_detail_fetch">抓取模式</label>
        <select id="skip_detail_fetch" name="skip_detail_fetch">
          <option value="true"${form.skip_detail_fetch ? " selected" : ""}>稳妥模式：跳过详情页，直接导出列表页</option>
          <option value="false"${!form.skip_detail_fetch ? " selected" : ""}>完整模式：进入详情页补全信息</option>
        </select>
      </div>
      <div class="field grid-2">
        <label class="check-row">
          <input name="filter_existing_output_early" type="checkbox"${form.filter_existing_output_early ? " checked" : ""} />
          <span>提前过滤 Excel 已有岗位</span>
        </label>
        <label class="check-row">
          <input name="refetch_crawled_details" type="checkbox"${form.refetch_crawled_details ? " checked" : ""} />
          <span>强制回补历史详情页</span>
        </label>
      </div>
      <div class="field">
        <label for="keywords">关键词</label>
        <textarea id="keywords" name="keywords" placeholder="多个关键词用英文逗号分隔">${escapeHtml(form.keywords)}</textarea>
      </div>
      <div class="field">
        <label for="regions">城市/地区</label>
        <textarea id="regions" name="regions" placeholder="不填则不限地区；多个城市用英文逗号分隔">${escapeHtml(form.regions)}</textarea>
      </div>
      <div class="field grid-4">
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
        <div>
          <label for="delay_between_pages">翻页等待秒</label>
          <input id="delay_between_pages" name="delay_between_pages" value="${escapeHtml(form.delay_between_pages)}" placeholder="1.8,3.0" />
        </div>
      </div>
      <label class="check-row">
        <input name="headless" type="checkbox"${form.headless ? " checked" : ""} />
        <span>无头浏览器运行</span>
      </label>
      <div class="muted">
        智联招聘任务会自动改为有界面运行。
        完整模式默认开启，会进入详情页补全“工作内容 / 任职要求”；如果验证过于频繁，再切回稳妥模式。
        “提前过滤”适合增量抓取；“强制回补”适合补历史表缺失详情，但风险和耗时都会更高。
      </div>
      <button class="primary" type="submit">${submitText}</button>
    </form>
  `;
}

function homePage(app) {
  const state = app.state;
  const running = state.tasks.filter(taskIsActive).length;
  const completed = state.tasks.filter((task) => task.status === "completed").length;
  const failed = state.tasks.filter((task) => task.status === "failed").length;
  return shell(app, `
    <div class="home-layout">
      <section class="panel form-panel">
        <div class="panel-title-row">
          <div class="panel-heading">
            <h2>参数设置</h2>
            <p>默认值优先读取 .env；未配置时会使用内置默认值。点击新增任务后会进入进行中的任务列表。</p>
          </div>
          <button class="secondary compact" type="button" data-action="reset-defaults">恢复默认值</button>
        </div>
        ${taskForm(state.form, "新增任务")}
      </section>
      <section class="panel settings-panel">
        <div class="panel-heading">
          <h2>任务概况</h2>
          <p>当前任务统计和最近刷新时间。</p>
        </div>
        <div class="summary-grid compact-summary">
          <div><b>${state.tasks.length}</b><span>全部任务</span></div>
          <div><b>${running}</b><span>进行中</span></div>
          <div><b>${completed}</b><span>已完成</span></div>
          <div><b>${failed}</b><span>失败</span></div>
        </div>
        <div class="settings-list">
          <div><span>默认平台</span><strong>${escapeHtml(state.defaults.platform)}</strong></div>
          <div><span>默认模式</span><strong>${escapeHtml(state.defaults.skip_detail_fetch ? "稳妥模式" : "完整模式")}</strong></div>
          <div><span>提前过滤已有 Excel</span><strong>${escapeHtml(state.defaults.filter_existing_output_early ? "开启" : "关闭")}</strong></div>
          <div><span>强制回补历史详情</span><strong>${escapeHtml(state.defaults.refetch_crawled_details ? "开启" : "关闭")}</strong></div>
          <div><span>默认关键词</span><strong>${escapeHtml(state.defaults.keywords || "未配置")}</strong></div>
          <div><span>默认地区</span><strong>${escapeHtml(state.defaults.regions || "不限地区")}</strong></div>
          <div><span>翻页等待</span><strong>${escapeHtml(state.defaults.delay_between_pages || "1.8,3.0")} 秒</strong></div>
        </div>
        ${state.lastRefresh ? `<div class="refresh-note">最近刷新：${escapeHtml(state.lastRefresh)}</div>` : ""}
        ${renderAuthCard(state.authZhaopin, { platformName: "\u667a\u8054\u62db\u8058", action: "start-zhaopin-login" })}
        ${renderAuthCard(state.auth51job, { platformName: "51job", action: "start-51job-login" })}
      </section>
    </div>
  `);
}

function tasksPage(app) {
  const state = app.state;
  const runningTasks = state.tasks.filter(taskIsActive);
  const liveTask = state.selectedTask || runningTasks[0] || null;
  const taskList = runningTasks.length
    ? runningTasks.map((task) => taskCard(task, state.selectedTask)).join("")
    : `<div class="empty">当前没有进行中的任务。可在首页设置参数并新增任务。</div>`;
  return shell(app, `
    <section class="panel task-panel">
      <div class="panel-title-row">
        <div class="panel-heading">
          <h2>进行中的任务</h2>
          <p>显示排队中和运行中的任务。点击任一任务进入详情页查看实时动态。</p>
        </div>
        <span class="pill">${runningTasks.length} 个进行中</span>
      </div>
      <div class="task-list task-list-wide">${taskList}</div>
      ${renderLogs(liveTask)}
      ${state.lastRefresh ? `<div class="refresh-note">最近刷新：${escapeHtml(state.lastRefresh)}</div>` : ""}
    </section>
  `);
}

function historyPage(app) {
  const state = app.state;
  const historyTasks = [...state.tasks].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  const pageSize = Number(state.historyPageSize || 8);
  const totalPages = Math.max(1, Math.ceil(historyTasks.length / pageSize));
  const currentPage = Math.min(Math.max(Number(state.historyPage || 1), 1), totalPages);
  if (state.historyPage !== currentPage) {
    state.historyPage = currentPage;
  }
  const pageStart = (currentPage - 1) * pageSize;
  const pageTasks = historyTasks.slice(pageStart, pageStart + pageSize);
  const rows = historyTasks.length
    ? pageTasks.map((task) => `
      <tr class="history-row" data-action="open-detail" data-id="${escapeHtml(task.id)}">
        <td><strong>${escapeHtml(task.name)}</strong><span>${escapeHtml(task.id)}</span></td>
        <td class="history-middle"><span class="status ${escapeHtml(task.status)}">${escapeHtml(statusText[task.status] || task.status)}</span></td>
        <td class="history-middle"><span class="platform-badge platform-${escapeHtml(task.platform)}">${escapeHtml(platformText(task.platform))}</span></td>
        <td class="history-middle">${escapeHtml(task.keywords.join(", "))}</td>
        <td class="history-middle">${escapeHtml(formatRegions(task.regions))}</td>
        <td class="history-middle">${escapeHtml(task.created_at || "-")}</td>
        <td class="history-middle">${escapeHtml(task.raw_count)} / ${escapeHtml(task.appended_count)} / ${escapeHtml(task.updated_count)}</td>
      </tr>
    `).join("")
    : "";
  return shell(app, `
    <section class="panel task-panel">
      <div class="panel-title-row">
        <div class="panel-heading">
          <h2>历史任务</h2>
          <p>以列表展示所有任务，点击一行进入任务详情。</p>
        </div>
        <span class="pill">${historyTasks.length} 条记录</span>
      </div>
      ${historyTasks.length ? `
        <div class="pagination-row">
          <div class="muted">第 ${currentPage} / ${totalPages} 页，每页 ${pageSize} 条，当前显示 ${pageStart + 1}-${pageStart + pageTasks.length} 条</div>
          <div class="pagination-actions">
            <button class="secondary compact" type="button" data-action="history-page" data-page="${currentPage - 1}"${currentPage <= 1 ? " disabled" : ""}>上一页</button>
            <button class="secondary compact" type="button" data-action="history-page" data-page="${currentPage + 1}"${currentPage >= totalPages ? " disabled" : ""}>下一页</button>
          </div>
        </div>
        <div class="table-wrap history-table-wrap">
          <table class="history-table">
            <thead>
              <tr>
                <th>任务</th>
                <th>状态</th>
                <th>平台</th>
                <th>关键词</th>
                <th>地区</th>
                <th>创建时间</th>
                <th>原始/新增/更新</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      ` : `<div class="empty">暂无历史任务。任务创建后会保存在这里。</div>`}
      ${state.lastRefresh ? `<div class="refresh-note">最近刷新：${escapeHtml(state.lastRefresh)}</div>` : ""}
    </section>
  `);
}

function detailPage(app) {
  const state = app.state;
  const task = state.selectedTask;
  if (!task) {
    return shell(app, `
      <section class="panel data-panel">
        <div class="panel-heading">
          <h2>任务详情</h2>
          <p>请选择一个任务。</p>
        </div>
        <div class="empty">没有选中的任务。</div>
      </section>
    `);
  }
  const dataBody = state.dataCollapsed
    ? ""
    : `
      <div class="file-row">${renderFiles(state.files)}</div>
      ${renderTable(state.data)}
    `;
  return shell(app, `
    <section class="panel detail-header-panel">
      <div class="panel-title-row">
        <div class="panel-heading">
          <h2>${escapeHtml(task.name)}</h2>
          <p>${escapeHtml(task.id)} · ${escapeHtml(task.created_at || "无创建时间")}</p>
        </div>
        <div class="panel-actions">
          <span class="status ${escapeHtml(task.status)}">${escapeHtml(statusText[task.status] || task.status)}</span>
          ${taskCanCancel(task) ? `<button class="secondary compact danger-action" type="button" data-action="cancel-task">中止任务</button>` : ""}
          ${task.status === "stopping" ? `<button class="secondary compact" type="button" disabled>中止中</button>` : ""}
          <button class="secondary compact" type="button" data-action="back">返回</button>
        </div>
      </div>
      <div class="detail-strip">
        <div><span>平台</span><strong>${escapeHtml(task.platform)}</strong></div>
        <div><span>关键词</span><strong>${escapeHtml(task.keywords.join(", "))}</strong></div>
        <div><span>地区</span><strong>${escapeHtml(formatRegions(task.regions))}</strong></div>
        <div><span>页数</span><strong>${escapeHtml(task.max_pages)}</strong></div>
        <div><span>模式</span><strong>${escapeHtml(task.headless ? "无头" : "有界面")}</strong></div>
        <div><span>抓取策略</span><strong>${escapeHtml(task.skip_detail_fetch ? "稳妥模式" : "完整模式")}</strong></div>
        <div><span>提前过滤</span><strong>${escapeHtml(task.filter_existing_output_early ? "开启" : "关闭")}</strong></div>
        <div><span>历史回补</span><strong>${escapeHtml(task.refetch_crawled_details ? "开启" : "关闭")}</strong></div>
        <div><span>开始</span><strong>${escapeHtml(task.started_at || "-")}</strong></div>
        <div><span>结束</span><strong>${escapeHtml(task.finished_at || "-")}</strong></div>
      </div>
    </section>

    <section class="panel detail-main-panel">
      <div class="panel-heading">
        <h2>运行情况</h2>
        <p>日志会随任务刷新实时更新。</p>
      </div>
      <div class="summary-grid compact-summary">
        <div><b>${escapeHtml(task.raw_count)}</b><span>原始</span></div>
        <div><b>${escapeHtml(task.appended_count)}</b><span>新增</span></div>
        <div><b>${escapeHtml(task.updated_count)}</b><span>更新</span></div>
        <div><b>${escapeHtml(state.lastRefresh || "-")}</b><span>最近刷新</span></div>
      </div>
      ${renderLogs(task)}
    </section>

    <section class="panel data-panel ${state.dataCollapsed ? "is-collapsed" : ""}">
      <div class="panel-title-row">
        <div class="panel-heading">
          <h2>Excel 数据</h2>
          <p>任务写出文件后，可预览前 100 行并下载完整 Excel。</p>
        </div>
        <button class="secondary compact" type="button" data-action="toggle-data">
          ${state.dataCollapsed ? "查看数据" : "折叠数据"}
        </button>
      </div>
      <div class="collapsible-body">${dataBody}</div>
    </section>
  `);
}

export function dashboard(app) {
  if (app.state.page === "tasks") return tasksPage(app);
  if (app.state.page === "history") return historyPage(app);
  if (app.state.page === "detail") return detailPage(app);
  return homePage(app);
}
