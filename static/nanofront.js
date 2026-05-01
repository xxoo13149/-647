export function createApp({ element, initialState, view, actions = {} }) {
  const target = typeof element === "string" ? document.querySelector(element) : element;
  const state = { ...initialState };
  let afterRender = () => {};

  if (!target) {
    throw new Error("NanoFront mount target not found.");
  }

  const app = {
    state,
    actions: {},
    html(strings, ...values) {
      return strings.reduce((output, string, index) => output + string + (values[index] ?? ""), "");
    },
    setState(patch) {
      Object.assign(state, typeof patch === "function" ? patch(state) : patch);
      app.render();
    },
    render() {
      target.innerHTML = view(app);
      afterRender(app);
    },
    onRender(callback) {
      afterRender = callback;
      return app;
    },
    mount() {
      app.render();
      return app;
    },
  };

  for (const [name, handler] of Object.entries(actions)) {
    app.actions[name] = (...args) => handler(app, ...args);
  }

  return app;
}

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
