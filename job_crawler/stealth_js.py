"""
Playwright 反检测注入脚本。在页面加载前隐藏自动化痕迹，模拟真实浏览器环境。
"""
from __future__ import annotations


# 完整反检测 JS —— 注入到每个页面 context 中
STEALTH_INIT_SCRIPT = r"""
// === 核心：隐藏 navigator.webdriver ===
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// === 模拟 Chrome 运行时对象 ===
window.chrome = window.chrome || { runtime: {} };
if (!window.chrome.runtime) {
    window.chrome.runtime = {};
}

// === 修复 navigator.plugins（Playwright Chromium 为空，真实 Chrome 有插件） ===
const makePluginArray = () => {
    const plugins = [
        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format', length: 1, },
        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '', length: 1, },
        { name: 'Native Client', filename: 'internal-nacl-plugin', description: '', length: 2, },
    ];
    const arr = Object.create(PluginArray.prototype);
    let _idx = 0;
    for (const p of plugins) {
        const plugin = Object.create(Plugin.prototype);
        const mimeArr = Object.create(MimeTypeArray.prototype);
        for (let i = 0; i < p.length; i++) {
            const mt = Object.create(MimeType.prototype);
            mt.type = 'application/x-unknown-content-type';
            mt.suffixes = '';
            mt.description = p.description;
            mt.enabledPlugin = plugin;
            mimeArr[i] = mt;
        }
        plugin.name = p.name;
        plugin.filename = p.filename;
        plugin.description = p.description;
        plugin.length = p.length;
        Object.defineProperty(plugin, '0', { get: () => mimeArr[0] });
        Object.defineProperty(plugin, '1', { get: () => mimeArr[1] });
        arr[_idx] = plugin;
        _idx++;
    }
    arr.length = plugins.length;

    const originalPlugins = Object.getOwnPropertyDescriptor(Navigator.prototype, 'plugins');
    if (originalPlugins && originalPlugins.configurable) {
        Object.defineProperty(Navigator.prototype, 'plugins', {
            get: () => arr,
            configurable: true,
            enumerable: true,
        });
    }
};
makePluginArray();

// === 修复 permissions.query 行为（防止通过权限检测自动化） ===
const originalQuery = window.navigator.permissions.query.bind(window.navigator.permissions);
window.navigator.permissions.query = (parameters) => {
    if (parameters.name === 'notifications') {
        return Promise.resolve({ state: Notification.permission, onchange: null });
    }
    return originalQuery(parameters);
};

// === 修复 headless 检测特征：window.outerWidth/outerHeight ===
Object.defineProperty(window, 'outerWidth', { get: () => window.innerWidth, configurable: true });
Object.defineProperty(window, 'outerHeight', { get: () => window.innerHeight, configurable: true });

// === 模拟真实的 screen 属性 ===
if (screen.width === 0 || screen.height === 0) {
    Object.defineProperty(screen, 'width', { get: () => 1920 });
    Object.defineProperty(screen, 'height', { get: () => 1080 });
}
if (screen.availWidth === 0 || screen.availHeight === 0) {
    Object.defineProperty(screen, 'availWidth', { get: () => 1920 });
    Object.defineProperty(screen, 'availHeight', { get: () => 1040 });
}
Object.defineProperty(screen, 'colorDepth', { get: () => 24 });
Object.defineProperty(screen, 'pixelDepth', { get: () => 24 });

// === 隐藏 HeadlessChrome UA 中的 "Headless" 标记 ===
// 如果 UA 包含 HeadlessChrome，已经由 project 层自定义 UA 处理，此处兜底
const currentUA = navigator.userAgent;
if (currentUA.includes('Headless')) {
    Object.defineProperty(navigator, 'userAgent', {
        get: () => currentUA.replace('HeadlessChrome', 'Chrome'),
    });
}

// === 添加缺失的 WebGL 属性（防止通过 WebGL 检测） ===
try {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
    if (gl) {
        const getParam = gl.getParameter.bind(gl);
        gl.getParameter = function(p) {
            if (p === 37445) return 'Google Inc.';  // UNMASKED_VENDOR_WEBGL
            if (p === 37446) return 'ANGLE (Intel, Intel(R) UHD Graphics Direct3D11 vs_5_0 ps_5_0)';  // UNMASKED_RENDERER_WEBGL
            return getParam(p);
        };
    }
} catch(e) {}

// === 防止通过 getBattery 检测（自动化常返回 always-charging） ===
if (navigator.getBattery) {
    const orig = navigator.getBattery.bind(navigator);
    navigator.getBattery = () => orig().then(b => {
        Object.defineProperty(b, 'charging', { get: () => true });
        Object.defineProperty(b, 'level', { get: () => 0.98 + Math.random() * 0.02 });
        return b;
    });
}

// === 修复 navigator.hardwareConcurrency ===
if (!navigator.hardwareConcurrency || navigator.hardwareConcurrency < 2) {
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
}

// === 修复 navigator.deviceMemory ===
if (!navigator.deviceMemory) {
    Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
}

// done
true;
"""
