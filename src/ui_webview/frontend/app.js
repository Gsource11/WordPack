(function () {
  const root = document.getElementById("app");
  const params = new URLSearchParams(window.location.search);
  const initialView = params.get("view") || "main";

  const state = {
    view: initialView,
    appTitle: "WordPack",
    branding: {
      appIconUrl: "",
      bubbleIconUrl: "",
    },
    themeMode: "light",
    ui: {
      status: "",
      translation_mode: "argos",
      theme_mode: "light",
      direction: "方向: 自动",
      history: [],
    },
    config: null,
    settings: null,
    settingsDraft: null,
    settingsOpen: false,
    historyOpen: false,
    zoomOpen: false,
    zoomPayload: null,
    sourceText: "",
    resultText: "",
    candidatePending: false,
    candidateItems: [],
    candidateReqId: 0,
    pending: false,
    mainReqId: 0,
    testingAi: false,
    aiTestState: "idle",
    aiAvailable: false,
    aiAvailableChecked: false,
    aiAvailabilityMessage: "",
    aiAvailabilityCheckedAt: 0,
    notice: null,
    toast: null,
    shortcuts: [],
    bubble: null,
    bubbleCandidatePaneOpen: false,
    triggerMode: "click",
    overlay: null,
    overlayReady: false,
    historyPanel: {
      tab: "recent",
      q: "",
      mode: "all",
      direction: "all",
      source_kind: "all",
      range_days: 0,
      offset: 0,
      limit: 50,
      total: 0,
      has_more: false,
      items: [],
      loading: false,
      filters: {
        directions: ["all"],
      },
      scrollTop: 0,
      viewportHeight: 420,
      rowHeight: 124,
      overscan: 4,
    },
  };

  const clone = (value) => JSON.parse(JSON.stringify(value));
  const escapeHtml = (value) =>
    String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");

  const icon = (paths) =>
    `<span class="icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.0" stroke-linecap="round" stroke-linejoin="round">${paths}</svg></span>`;

  const icons = {
    history: icon("<path d='M3 12a9 9 0 1 0 3-6.7'/><path d='M3 4v5h5'/><path d='M12 7v5l3 2'/>"),
    search: icon("<circle cx='11' cy='11' r='7'/><path d='m20 20-3.5-3.5'/>"),
    settings: icon("<circle cx='12' cy='12' r='3.2'/><path d='M19.4 15a1 1 0 0 0 .2 1.1l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1 1 0 0 0-1.1-.2 1 1 0 0 0-.6.9V20a2 2 0 1 1-4 0v-.1a1 1 0 0 0-.6-.9 1 1 0 0 0-1.1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1 1 0 0 0 .2-1.1 1 1 0 0 0-.9-.6H4a2 2 0 1 1 0-4h.1a1 1 0 0 0 .9-.6 1 1 0 0 0-.2-1.1l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1 1 0 0 0 1.1.2 1 1 0 0 0 .6-.9V4a2 2 0 1 1 4 0v.1a1 1 0 0 0 .6.9 1 1 0 0 0 1.1-.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1 1 0 0 0-.2 1.1 1 1 0 0 0 .9.6H20a2 2 0 1 1 0 4h-.1a1 1 0 0 0-.9.6Z'/>"),
    close: icon("<path d='M6 6l12 12M18 6l-12 12'/>"),
    book: icon("<path d='M4.5 6a1.5 1.5 0 0 1 1.5-1.5H8c1.8 0 3.2.45 4 1.35.8-.9 2.2-1.35 4-1.35h2a1.5 1.5 0 0 1 1.5 1.5v11.4a.6.6 0 0 1-.6.6H16c-1.6 0-2.85.3-3.75.92a.45.45 0 0 1-.5 0C10.85 18.3 9.6 18 8 18H5.1a.6.6 0 0 1-.6-.6Z'/><path d='M12 5.9V18.9'/><path d='M7.4 8.1h1.3M15.3 8.1h1.3'/>"),
    robot: icon("<path d='M12 4v3'/><rect x='4' y='7' width='16' height='12' rx='4'/><path d='M9 12h.01M15 12h.01M8 16h8'/>"),
    candidates: icon("<path d='M7 7h10M7 12h7M7 17h5'/><circle cx='18' cy='12' r='2'/><circle cx='16.5' cy='17' r='1.5'/><circle cx='18.5' cy='7' r='1.5'/>"),
    copy: icon("<rect x='9' y='9' width='10' height='10' rx='2'/><path d='M5 15V7a2 2 0 0 1 2-2h8'/>"),
    trash: icon("<path d='M4 7h16'/><path d='M10 11v6M14 11v6'/><path d='M6 7l1 12h10l1-12'/><path d='M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2'/>"),
    expand: icon("<path d='M15 4h5v5M9 20H4v-5M20 15v5h-5M4 9V4h5'/><path d='M14 10l6-6M10 14l-6 6'/>"),
    pin: icon("<path d='M12 17v3'/><path d='M8 4h8l-1 5 3 3H6l3-3Z'/>"),
    favorite: icon("<path d='m12 17.3-5.2 3 1.4-5.8L3.8 10l5.9-.5L12 4l2.3 5.5 5.9.5-4.4 4.5 1.4 5.8Z'/>"),
    favoriteActive: "<span class='icon'><svg viewBox='0 0 24 24' fill='currentColor' stroke='currentColor' stroke-width='1.4' stroke-linecap='round' stroke-linejoin='round'><path d='m12 17.3-5.2 3 1.4-5.8L3.8 10l5.9-.5L12 4l2.3 5.5 5.9.5-4.4 4.5 1.4 5.8Z'/></svg></span>",
    camera: icon("<path d='M4 8h4l2-2h4l2 2h4v10H4Z'/><circle cx='12' cy='13' r='3.5'/>"),
    clipboard: icon("<rect x='8' y='4' width='8' height='4' rx='1.5'/><path d='M9 6H7a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-2'/>"),
    moon: icon("<path d='M21 12.7A9 9 0 1 1 11.3 3a7 7 0 0 0 9.7 9.7Z'/>"),
    sun: icon("<circle cx='12' cy='12' r='4'/><path d='M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4'/>"),
    link: icon("<path d='M10 14 14 10'/><path d='M8.5 6.5a3 3 0 0 1 4.2 0l.8.8a3 3 0 0 1 0 4.2l-.6.6'/><path d='M11.1 12.9l-.6.6a3 3 0 0 1-4.2 0l-.8-.8a3 3 0 0 1 0-4.2l.6-.6'/>"),
    check: icon("<path d='M20 6 9 17l-5-5'/>"),
    error: icon("<circle cx='12' cy='12' r='9'/><path d='M9 9l6 6M15 9l-6 6'/>"),
  };

  const brandIcon = (className, url, fallback = "") => {
    if (!url) return fallback;
    return `<span class="brand-icon ${className}"><img src="${url}" alt="${escapeHtml(state.appTitle || "WordPack")}" /></span>`;
  };

  const apiCall = async (method, ...args) => {
    if (!window.pywebview || !window.pywebview.api || typeof window.pywebview.api[method] !== "function") {
      return null;
    }
    return window.pywebview.api[method](...args);
  };

  const skeletonMarkup = (variant = "main") => `
    <div class="result-skeleton ${variant}">
      <span class="skeleton-line w-88"></span>
      <span class="skeleton-line w-82"></span>
      <span class="skeleton-line w-74"></span>
      <span class="skeleton-line w-58"></span>
    </div>`;

  const scrollFollowState = {
    "main-result": true,
    "bubble-result": true,
    "zoom-shell": true,
  };
  const scrollTailTolerance = 18;
  let tooltipEl = null;
  let tooltipTarget = null;

  const choiceGroupMarkup = (field, currentValue, options) => `
    <div class="choice-group">
      ${options.map((option) => `
        <button
          class="choice-chip ${String(currentValue ?? "") === String(option.value ?? "") ? "active" : ""}"
          data-action="set-setting-choice"
          data-field="${escapeHtml(field)}"
          data-value="${escapeHtml(option.value)}"
          type="button"
        >${escapeHtml(option.label)}</button>`).join("")}
    </div>`;

  const historySourceLabel = (value) => {
    switch (String(value || "").toLowerCase()) {
      case "selection":
        return "划词";
      case "screenshot":
        return "截图";
      case "manual":
      default:
        return "手动";
    }
  };

  const historyModeLabel = (value) => (String(value || "").toLowerCase() === "ai" ? "AI" : "词典");
  const formatDateTime = (timestampSec) => {
    const ms = Number(timestampSec || 0) * 1000;
    if (!Number.isFinite(ms) || ms <= 0) return "未检测";
    const d = new Date(ms);
    if (Number.isNaN(d.getTime())) return "未检测";
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  };

  const candidateThresholds = () => ({
    cn: Number(state.config?.openai?.multi_candidate_short_cn_max_chars || 24),
    enWords: Number(state.config?.openai?.multi_candidate_short_en_max_words || 12),
  });

  function isShortForCandidates(text) {
    const value = String(text || "").trim();
    if (!value) return false;
    const sentenceMarks = (value.match(/[.!?\u3002\uFF01\uFF1F]/g) || []).length;
    if (sentenceMarks > 1) return false;
    const thresholds = candidateThresholds();
    const hasCjk = /[\u4e00-\u9fff]/.test(value);
    if (hasCjk) {
      const compact = value.replace(/\s+/g, "");
      return compact.length <= Math.max(8, thresholds.cn);
    }
    const words = value.match(/[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)?/g) || [];
    if (words.length) {
      return words.length <= Math.max(4, thresholds.enWords);
    }
    return value.length <= Math.max(8, thresholds.cn);
  }

  function candidateAvailability({ mode, sourceText, pending, hasResult, aiAvailable, aiAvailableChecked }) {
    const currentMode = String(mode || "").toLowerCase();
    const canUseAi = aiAvailable === true;
    if (!canUseAi) {
      return {
        enabled: false,
        tip: "",
      };
    }
    if (!String(sourceText || "").trim()) {
      return { enabled: false, tip: "请先输入并翻译" };
    }
    if (pending) {
      return { enabled: false, tip: "翻译进行中，请稍后" };
    }
    if (!hasResult) {
      return { enabled: false, tip: "请先完成一次翻译" };
    }
    if (!isShortForCandidates(sourceText)) {
      return { enabled: false, tip: "仅短词或短句支持多候选" };
    }
    if (currentMode !== "ai" && canUseAi) {
      return { enabled: true, tip: "将自动切换到 AI 并生成 4 个候选译文" };
    }
    return { enabled: true, tip: "生成 4 个候选译文" };
  }

  function argosAvailability() {
    const settings = state.settings || {};
    const hasRuntimeFlag = typeof settings.offlineRuntimeReady === "boolean";
    const hasModelsFlag = Array.isArray(settings.offlineModels);
    // Bubble view may bootstrap without settings payload; avoid false-negative disable.
    if (!hasRuntimeFlag && !hasModelsFlag) {
      return { available: true, tip: "" };
    }
    const offlineModels = hasModelsFlag ? settings.offlineModels : [];
    const hasOfflineModels = offlineModels.length > 0;
    const runtimeReady = Boolean(settings.offlineRuntimeReady);
    const available = runtimeReady && hasOfflineModels;
    return {
      available,
      tip: available ? "" : "模式不可用，请检查配置",
    };
  }

  function aiAvailability() {
    const available = Boolean(state.aiAvailable);
    return {
      available,
      tip: available ? "" : "模式不可用，请检查配置",
    };
  }

  function modeAvailability(mode) {
    const normalized = String(mode || "").toLowerCase();
    return normalized === "ai" ? aiAvailability() : argosAvailability();
  }

  function anyModeAvailable() {
    const argos = argosAvailability();
    const ai = aiAvailability();
    return Boolean(argos.available || ai.available);
  }
  const formatAllCandidates = (items) =>
    (Array.isArray(items) ? items : [])
      .map((item, index) => `${index + 1}. ${String(item || "").trim()}`)
      .filter(Boolean)
      .join("\n");
  const isSuppressedAiNotice = (text) => /AI\s*不可用/.test(String(text || ""));
  function normalizeCandidateText(value) {
    return String(value || "")
      .trim()
      .toLowerCase()
      .replace(/[\s`~!@#$%^&*()_\-+=\[\]{}|\\:;"'<>,.?/·，。！？；：“”‘’、（）【】《》…—]+/g, "");
  }

  function dedupeCandidateItems(items) {
    const out = [];
    const seen = new Set();
    for (const raw of Array.isArray(items) ? items : []) {
      const value = String(raw || "").trim();
      if (!value) continue;
      const key = normalizeCandidateText(value);
      if (!key || seen.has(key)) continue;
      seen.add(key);
      out.push(value);
    }
    return out;
  }

  function getMainDisplayedCandidates() {
    return dedupeCandidateItems(state.candidateItems).slice(0, 4);
  }

  function getBubbleDisplayedCandidates() {
    return dedupeCandidateItems(state.bubble?.candidate_items || []).slice(0, 4);
  }

  function historyQueryPayload(reset = false) {
    const panel = state.historyPanel;
    return {
      tab: panel.tab,
      q: panel.q,
      mode: panel.mode,
      direction: panel.direction,
      source_kind: panel.source_kind,
      range_days: panel.range_days,
      limit: panel.limit,
      offset: reset ? 0 : panel.offset,
    };
  }

  async function loadHistory(reset = false) {
    const panel = state.historyPanel;
    if (panel.loading) return;
    const activeElement = document.activeElement;
    const shouldRefocusSearch = keepHistorySearchFocus
      || (activeElement instanceof HTMLInputElement && activeElement.classList.contains("history-search"));
    keepHistorySearchFocus = false;
    panel.loading = true;
    if (reset) {
      panel.offset = 0;
      panel.items = [];
      panel.scrollTop = 0;
    }
    rerender();
    try {
      const payload = await apiCall("list_history", historyQueryPayload(reset));
      if (!payload) return;
      const incoming = Array.isArray(payload.items) ? payload.items : [];
      panel.items = reset ? incoming : [...panel.items, ...incoming];
      panel.total = Number(payload.total || panel.items.length);
      panel.has_more = Boolean(payload.has_more);
      panel.offset = panel.items.length;
      if (payload.filters?.directions?.length) {
        panel.filters.directions = payload.filters.directions;
        if (!panel.filters.directions.includes(panel.direction)) {
          panel.direction = "all";
        }
      }
      rerender();
      if (shouldRefocusSearch) {
        window.requestAnimationFrame(() => {
          const input = document.querySelector(".history-search");
          if (input instanceof HTMLInputElement) {
            input.focus();
            const end = input.value.length;
            input.setSelectionRange(end, end);
          }
        });
      }
    } finally {
      panel.loading = false;
      rerender();
      if (shouldRefocusSearch) {
        window.requestAnimationFrame(() => {
          const input = document.querySelector(".history-search");
          if (input instanceof HTMLInputElement) {
            input.focus();
            const end = input.value.length;
            input.setSelectionRange(end, end);
          }
        });
      }
    }
  }

  function isNearScrollEnd(element) {
    return (element.scrollTop + element.clientHeight) >= (element.scrollHeight - scrollTailTolerance);
  }

  function applyAutoScroll(key) {
    const element = document.querySelector(`[data-autoscroll="${key}"]`);
    if (!element || scrollFollowState[key] === false) return;
    element.scrollTop = element.scrollHeight;
  }

  function desiredMainCompact() {
    if (Date.now() < forceMainExpandedUntil) {
      return false;
    }
    return (
      state.view === "main"
      && !state.settingsOpen
      && !state.historyOpen
      && !state.zoomOpen
      && !state.pending
      && !state.resultText
    );
  }

  function ensureTooltipElement() {
    if (tooltipEl && document.body.contains(tooltipEl)) {
      return tooltipEl;
    }
    tooltipEl = document.createElement("div");
    tooltipEl.className = "app-tooltip";
    tooltipEl.setAttribute("role", "tooltip");
    tooltipEl.setAttribute("aria-hidden", "true");
    document.body.appendChild(tooltipEl);
    return tooltipEl;
  }

  function positionTooltip() {
    if (!tooltipEl || !tooltipTarget || !document.body.contains(tooltipTarget)) return;
    const rect = tooltipTarget.getBoundingClientRect();
    const gap = 8;
    const vw = window.innerWidth || document.documentElement.clientWidth || 0;
    const vh = window.innerHeight || document.documentElement.clientHeight || 0;
    const maxWidth = Math.min(320, Math.max(160, vw - 24));
    tooltipEl.style.maxWidth = `${maxWidth}px`;
    tooltipEl.style.left = "-9999px";
    tooltipEl.style.top = "-9999px";
    tooltipEl.classList.add("open");
    const tipRect = tooltipEl.getBoundingClientRect();
    let left = rect.left + (rect.width - tipRect.width) / 2;
    left = Math.max(8, Math.min(left, vw - tipRect.width - 8));
    let top = rect.top - tipRect.height - gap;
    if (top < 8) {
      top = Math.min(vh - tipRect.height - 8, rect.bottom + gap);
    }
    tooltipEl.style.left = `${Math.round(left)}px`;
    tooltipEl.style.top = `${Math.round(top)}px`;
  }

  function hideTooltip() {
    if (!tooltipEl) return;
    tooltipTarget = null;
    tooltipEl.classList.remove("open");
    tooltipEl.setAttribute("aria-hidden", "true");
  }

  function showTooltip(target, text) {
    if (!target || !text) {
      hideTooltip();
      return;
    }
    const el = ensureTooltipElement();
    tooltipTarget = target;
    el.textContent = text;
    el.setAttribute("aria-hidden", "false");
    positionTooltip();
  }

  function installTooltipDelegation() {
    if (document.body.dataset.tooltipDelegated === "1") return;
    document.body.dataset.tooltipDelegated = "1";

    document.addEventListener("mouseover", (event) => {
      const target = event.target?.closest?.("[data-tooltip]");
      const text = target?.dataset?.tooltip || "";
      if (target && text) {
        showTooltip(target, text);
      }
    });

    document.addEventListener("mouseout", (event) => {
      if (!tooltipTarget) return;
      const to = event.relatedTarget;
      if (to instanceof Element && tooltipTarget.contains(to)) return;
      if (event.target instanceof Element && tooltipTarget.contains(event.target)) {
        hideTooltip();
      }
    });

    document.addEventListener("focusin", (event) => {
      const target = event.target?.closest?.("[data-tooltip]");
      const text = target?.dataset?.tooltip || "";
      if (target && text) {
        showTooltip(target, text);
      }
    });

    document.addEventListener("focusout", (event) => {
      if (!tooltipTarget) return;
      if (event.target instanceof Element && tooltipTarget.contains(event.target)) {
        hideTooltip();
      }
    });

    window.addEventListener("scroll", () => {
      if (tooltipTarget) positionTooltip();
    }, true);

    window.addEventListener("resize", () => {
      if (tooltipTarget) positionTooltip();
    });
  }

  function playMainContentExpand() {
    if (mainContentExpandTimer) {
      window.clearTimeout(mainContentExpandTimer);
      mainContentExpandTimer = 0;
    }
    document.body.dataset.mainContentExpanding = "1";
    mainContentExpandTimer = window.setTimeout(() => {
      delete document.body.dataset.mainContentExpanding;
      mainContentExpandTimer = 0;
    }, 220);
  }

  function flushMainCompactRequest() {
    if (state.view !== "main") return;
    if (!mainWindowCompactTarget || mainWindowCompactInFlight) return;
    if (mainWindowCompactTarget === mainWindowCompactApplied) return;

    const requestKey = mainWindowCompactTarget;
    const desired = requestKey === "true";
    const reqId = ++mainWindowCompactReqId;
    mainWindowCompactInFlight = true;

    void apiCall("set_main_compact", desired)
      .then((payload) => {
        if (reqId !== mainWindowCompactReqId) return;
        mainWindowCompactApplied = requestKey;
        if (!desired && (payload?.ok ?? true)) {
          playMainContentExpand();
        }
      })
      .catch(() => {})
      .finally(() => {
        if (reqId !== mainWindowCompactReqId) return;
        mainWindowCompactInFlight = false;
        if (mainWindowCompactTarget !== mainWindowCompactApplied) {
          window.requestAnimationFrame(flushMainCompactRequest);
        }
      });
  }

  function syncMainCompact() {
    if (state.view !== "main") return;
    mainWindowCompactTarget = desiredMainCompact() ? "true" : "false";
    flushMainCompactRequest();
  }

  function clearSideSheetOpenTimer() {
    if (!sideSheetOpenRaf) return;
    window.cancelAnimationFrame(sideSheetOpenRaf);
    sideSheetOpenRaf = 0;
  }

  function stageOpenSideSheet(kind) {
    clearSideSheetOpenTimer();
    forceMainExpandedUntil = Date.now() + 320;
    state.historyOpen = kind === "history";
    state.settingsOpen = kind === "settings";
    mainWindowCompactTarget = "false";
    rerender();
    flushMainCompactRequest();
    return new Promise((resolve) => {
      resolve();
    });
  }

  async function openHistoryPanel() {
    await stageOpenSideSheet("history");
    void loadHistory(true);
  }

  async function openSettingsPanel() {
    state.settingsDraft = clone(state.config || {});
    state.notice = null;
    state.testingAi = false;
    state.aiTestState = "idle";
    await stageOpenSideSheet("settings");
    const payload = await apiCall("load_settings");
    if (payload) {
      if (payload.config) state.config = payload.config;
      state.settings = payload;
      state.settingsDraft = clone(payload.config || state.config || {});
      rerender();
    }
  }

  function showMainFromTray() {
    clearSideSheetOpenTimer();
    state.historyOpen = false;
    state.settingsOpen = false;
    state.zoomOpen = false;
    rerender();
    syncMainCompact();
    window.requestAnimationFrame(() => {
      const input = document.getElementById("sourceText");
      if (input instanceof HTMLTextAreaElement) {
        input.focus();
      }
    });
  }

  function updateResultCardVisibility() {
    const resultCard = document.querySelector(".result-card");
    if (!resultCard) return false;
    const shouldShow = Boolean(state.pending || state.resultText);
    resultCard.classList.toggle("hidden", !shouldShow);
    const windowCard = document.querySelector(".window-card");
    if (windowCard) {
      windowCard.classList.toggle("no-result", !shouldShow);
    }
    return shouldShow;
  }

  function captureShortcut(event) {
    const key = String(event.key || "").trim();
    if (!key) return null;
    if (key === "Backspace" || key === "Delete" || key === "Escape") {
      return "";
    }
    const upper = key.length === 1 ? key.toUpperCase() : key.toUpperCase();
    if (["CONTROL", "SHIFT", "ALT", "META"].includes(upper)) {
      return null;
    }
    let normalizedKey = "";
    if (/^[A-Z]$/.test(upper) || /^[0-9]$/.test(upper) || /^F([1-9]|1[0-2])$/.test(upper)) {
      normalizedKey = upper;
    }
    if (!normalizedKey) {
      return null;
    }
    const modifiers = [];
    if (event.ctrlKey) modifiers.push("CTRL");
    if (event.altKey) modifiers.push("ALT");
    if (event.shiftKey) modifiers.push("SHIFT");
    if (!modifiers.length) {
      return null;
    }
    return [...modifiers, normalizedKey].join("+");
  }

  let toastTimer = 0;
  let mainWindowCompactApplied = "";
  let mainWindowCompactTarget = "";
  let mainWindowCompactInFlight = false;
  let mainWindowCompactReqId = 0;
  let historyFilterTimer = 0;
  let settingsSaveTimer = 0;
  let settingsSaveInFlight = false;
  let settingsSaveQueued = false;
  let keepHistorySearchFocus = false;
  let sideSheetOpenRaf = 0;
  let mainContentExpandTimer = 0;
  let forceMainExpandedUntil = 0;
  const HISTORY_SEARCH_DEBOUNCE_MS = 500;
  const SETTINGS_SAVE_DEBOUNCE_MS = 260;

  function shouldRunHistorySearch(value) {
    const query = String(value || "").trim();
    return query.length === 0 || query.length >= 2;
  }

  function scheduleHistorySearch(immediate = false) {
    if (historyFilterTimer) {
      window.clearTimeout(historyFilterTimer);
      historyFilterTimer = 0;
    }
    if (!shouldRunHistorySearch(state.historyPanel.q)) {
      return;
    }
    const run = () => {
      historyFilterTimer = 0;
      void loadHistory(true);
    };
    if (immediate) {
      run();
      return;
    }
    historyFilterTimer = window.setTimeout(run, HISTORY_SEARCH_DEBOUNCE_MS);
  }

  async function flushSettingsDraftSave() {
    if (!state.settingsDraft) return;
    if (settingsSaveInFlight) {
      settingsSaveQueued = true;
      return;
    }
    settingsSaveInFlight = true;
    try {
      await apiCall("save_settings", state.settingsDraft);
    } finally {
      settingsSaveInFlight = false;
      if (settingsSaveQueued) {
        settingsSaveQueued = false;
        void flushSettingsDraftSave();
      }
    }
  }

  function scheduleSettingsSave(immediate = false) {
    if (!state.settingsOpen || !state.settingsDraft) return;
    if (settingsSaveTimer) {
      window.clearTimeout(settingsSaveTimer);
      settingsSaveTimer = 0;
    }
    const run = () => {
      settingsSaveTimer = 0;
      void flushSettingsDraftSave();
    };
    if (immediate) {
      run();
      return;
    }
    settingsSaveTimer = window.setTimeout(run, SETTINGS_SAVE_DEBOUNCE_MS);
  }

  function showToast(type, text) {
    if (isSuppressedAiNotice(text)) {
      return;
    }
    state.toast = { type: type || "", text: text || "" };
    if (toastTimer) {
      window.clearTimeout(toastTimer);
    }
    toastTimer = window.setTimeout(() => {
      state.toast = null;
      rerender();
    }, 4200);
  }

  function setTheme(themeMode) {
    state.themeMode = themeMode || "light";
    document.body.dataset.theme = state.themeMode;
  }

  function applyBootstrap(payload) {
    state.view = payload.view || state.view;
    if (payload.appTitle) state.appTitle = payload.appTitle;
    if (payload.branding) state.branding = payload.branding;
    if (payload.ui) state.ui = payload.ui;
    if (payload.config) {
      state.config = payload.config;
      setTheme(payload.themeMode || payload.settings?.effectiveTheme || "light");
      state.ui.translation_mode = payload.config.translation_mode;
      if (!state.settingsDraft) {
        state.settingsDraft = clone(payload.config);
      }
    }
    if (payload.settings) {
      state.settings = payload.settings;
      if (payload.settings.historyFilters?.directions?.length) {
        state.historyPanel.filters.directions = payload.settings.historyFilters.directions;
      }
    }
    if (payload.aiAvailability) {
      state.aiAvailable = Boolean(payload.aiAvailability.available);
      state.aiAvailableChecked = Boolean(payload.aiAvailability.checked);
      state.aiAvailabilityMessage = String(payload.aiAvailability.message || "");
      state.aiAvailabilityCheckedAt = Number(payload.aiAvailability.checkedAt || 0);
    }
    if (payload.shortcuts) state.shortcuts = payload.shortcuts;
    if (payload.bubble) state.bubble = payload.bubble;
    if (payload.triggerMode) state.triggerMode = payload.triggerMode;
    if (payload.overlay) state.overlay = payload.overlay;
    state.ui.theme_mode = state.themeMode;
    document.title = state.appTitle || "WordPack";
  }

  function captureScrollPositions() {
    const positions = {};
    document.querySelectorAll("[data-preserve-scroll]").forEach((element) => {
      const key = element.getAttribute("data-preserve-scroll");
      positions[key] = element.scrollTop;
    });
    return positions;
  }

  function restoreScrollPositions(positions) {
    if (!positions) return;
    document.querySelectorAll("[data-preserve-scroll]").forEach((element) => {
      const key = element.getAttribute("data-preserve-scroll");
      if (Object.prototype.hasOwnProperty.call(positions, key)) {
        element.scrollTop = positions[key];
      }
    });
  }

  function rerender() {
    hideTooltip();
    const focusState = captureFocusState();
    const scrollPositions = captureScrollPositions();
    render();
    restoreScrollPositions(scrollPositions);
    restoreFocusState(focusState);
    installDragHandles();
    syncMainCompact();
    window.requestAnimationFrame(() => {
      applyAutoScroll("main-result");
      applyAutoScroll("bubble-result");
      applyAutoScroll("zoom-shell");
    });
  }

  function setValue(obj, path, value) {
    const keys = path.split(".");
    let current = obj;
    keys.forEach((key, index) => {
      if (index === keys.length - 1) {
        current[key] = value;
      } else {
        current[key] = current[key] || {};
        current = current[key];
      }
    });
  }

  function renderIcon() {
    const iconUrl = state.branding?.bubbleIconUrl || state.branding?.appIconUrl || "";
    const iconInner = iconUrl
      ? `<img class="selection-icon-image" src="${iconUrl}" alt="${escapeHtml(state.appTitle || "WordPack")}" />`
      : icons.book;
    root.innerHTML = `
      <div class="icon-shell">
        <button class="selection-icon active" data-action="trigger-selection" aria-label="划词翻译">
          <span class="selection-icon-inner">${iconInner}</span>
        </button>
      </div>`;
    if (state.triggerMode === "hover") {
      const button = document.querySelector(".selection-icon");
      if (button) {
        button.addEventListener("mouseenter", () => {
          void apiCall("trigger_selection_translate");
        }, { once: true });
      }
    }
  }

  function renderOverlay() {
    const background = state.overlay?.backgroundDataUrl || "";
    root.innerHTML = `
      <div class="overlay-shell" id="overlayRoot">
        <div class="overlay-background" style="background-image:url('${background}')"></div>
        <div class="overlay-mask"></div>
        <div class="overlay-hint">拖拽选择截图区域 · 右键 / Esc 取消</div>
        <div class="overlay-selection hidden" id="overlaySelection" data-size=""></div>
      </div>`;
    if (!state.overlayReady) {
      initOverlayInteractions();
      state.overlayReady = true;
    }
  }

  function render() {
    document.body.className = `view-${state.view}`;
    document.documentElement.dataset.view = state.view;
    if (state.view === "bubble") {
      renderBubble();
      return;
    }
    if (state.view === "icon") {
      renderIcon();
      return;
    }
    if (state.view === "overlay") {
      renderOverlay();
      return;
    }
    renderMain();
  }

  function installDragHandles() {
    if (!window.pywebview || typeof window.pywebview._jsApiCallback !== "function") {
      return;
    }

    document.querySelectorAll("[data-drag-handle]").forEach((element) => {
      if (element.dataset.dragBound === "true") return;
      element.dataset.dragBound = "true";
      element.addEventListener("mousedown", (event) => {
        if (event.button !== 0) return;
        if (event.target.closest("button, input, textarea, select, label, a, [data-no-drag]")) {
          return;
        }
        const initialX = event.clientX;
        const initialY = event.clientY;

        const onMove = (moveEvent) => {
          const x = moveEvent.screenX - initialX;
          const y = moveEvent.screenY - initialY;
          window.pywebview._jsApiCallback("pywebviewMoveWindow", [x, y], "move");
        };

        const onUp = () => {
          window.removeEventListener("mousemove", onMove);
          window.removeEventListener("mouseup", onUp);
        };

        window.addEventListener("mousemove", onMove);
        window.addEventListener("mouseup", onUp, { once: true });
      });
    });
  }

  function initOverlayInteractions() {
    const overlayRoot = document.getElementById("overlayRoot");
    const selection = document.getElementById("overlaySelection");
    if (!overlayRoot || !selection) return;

    let active = false;
    let startX = 0;
    let startY = 0;

    const bounds = state.overlay?.bounds || { left: 0, top: 0, width: overlayRoot.clientWidth, height: overlayRoot.clientHeight };
    const toGlobal = (x, y) => ({
      x: Math.round(bounds.left + (x / overlayRoot.clientWidth) * bounds.width),
      y: Math.round(bounds.top + (y / overlayRoot.clientHeight) * bounds.height),
    });

    const draw = (x, y) => {
      const left = Math.min(startX, x);
      const top = Math.min(startY, y);
      const width = Math.abs(x - startX);
      const height = Math.abs(y - startY);
      selection.classList.remove("hidden");
      selection.style.left = `${left}px`;
      selection.style.top = `${top}px`;
      selection.style.width = `${width}px`;
      selection.style.height = `${height}px`;
      selection.dataset.size = `${Math.round((width / overlayRoot.clientWidth) * bounds.width)} x ${Math.round((height / overlayRoot.clientHeight) * bounds.height)}`;
    };

    overlayRoot.addEventListener("mousedown", (event) => {
      if (event.button !== 0) return;
      active = true;
      startX = event.clientX;
      startY = event.clientY;
      draw(startX, startY);
    });

    overlayRoot.addEventListener("mousemove", (event) => {
      if (!active) return;
      draw(event.clientX, event.clientY);
    });

    overlayRoot.addEventListener("mouseup", (event) => {
      if (!active) return;
      active = false;
      const left = Math.min(startX, event.clientX);
      const top = Math.min(startY, event.clientY);
      const right = Math.max(startX, event.clientX);
      const bottom = Math.max(startY, event.clientY);
      const start = toGlobal(left, top);
      const end = toGlobal(right, bottom);
      void apiCall("finish_screenshot_selection", { left: start.x, top: start.y, right: end.x, bottom: end.y });
    });

    overlayRoot.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      void apiCall("cancel_screenshot_selection");
    });

    document.addEventListener("keydown", (event) => {
      if (state.view === "overlay" && event.key === "Escape") {
        void apiCall("cancel_screenshot_selection");
      }
    });
  }

  function renderMain() {
    const mode = state.config?.translation_mode || "argos";
    const result = state.resultText || "";
    const showResultCard = Boolean(state.pending || result);
    const mainResultClass = state.pending ? (result ? "pending-streaming" : "pending-waiting") : "";
    const mainResultContent = state.pending && !result ? skeletonMarkup("main") : escapeHtml(result);
    const historyPanel = state.historyPanel;
    const settings = state.settings || { offlineModels: [], offlineRuntimeReady: false, offlineRuntimeHint: "" };
    const argosModeAvailability = modeAvailability("argos");
    const aiModeAvailability = modeAvailability("ai");
    const translateEnabled = anyModeAvailable();
    const offlineModels = Array.isArray(settings.offlineModels) ? settings.offlineModels : [];
    const hasOfflineModels = offlineModels.length > 0;
    const argosNoticeClass = (!hasOfflineModels || !settings.offlineRuntimeReady) ? "warning" : "";
    const argosNoticeHtml = !hasOfflineModels
      ? `未检测到已导入的 Argos 模型。请到 <a href="https://www.argosopentech.com/argospm/index/" target="_blank" rel="noopener noreferrer">Argos 模型下载页</a> 下载模型后，再点击“导入 Argos 模型”。`
      : escapeHtml(settings.offlineRuntimeReady ? (settings.offlineDiagnostics || "Argos 运行库可用") : (settings.offlineRuntimeHint || "Argos 运行库未就绪"));
    const draft = state.settingsDraft || clone(state.config || {});
    const selectionEnabled = draft.interaction?.selection_enabled !== false;
    const selectionTriggerMode = draft.interaction?.selection_trigger_mode || "icon";
    const screenshotEnabled = draft.interaction?.screenshot_enabled !== false;
    const screenshotHotkey = draft.interaction?.screenshot_hotkey ?? "";
    const directionOptions = Array.from(new Set([
      "auto",
      ...(settings.offlineModels || []).map((item) => item.direction).filter(Boolean),
    ])).map((value) => ({ value, label: value }));
    const selectionModeOptions = [
      { value: "icon", label: "图标触发" },
      { value: "double_ctrl", label: "双击 Ctrl" },
    ];
    const iconTriggerOptions = [
      { value: "click", label: "点击" },
      { value: "hover", label: "悬停" },
    ];
    const historyDirectionOptions = (historyPanel.filters?.directions || ["all"]).map((value) => ({
      value,
      label: value === "all" ? "全部方向" : value,
    }));
    const aiTestIcon = state.testingAi ? icons.link : (state.aiTestState === "success" ? icons.check : state.aiTestState === "error" ? icons.error : icons.link);
    const aiStatusText = state.aiAvailableChecked ? (state.aiAvailable ? "可用" : "不可用") : "检测中";
    const aiStatusTime = formatDateTime(state.aiAvailabilityCheckedAt);
    const notice = state.notice
      ? `<div class="notice ${escapeHtml(state.notice.type || "")}">${escapeHtml(state.notice.text || "")}</div>`
      : "";
    const toast = state.toast
      ? `<div class="global-toast ${escapeHtml(state.toast.type || "")}">${escapeHtml(state.toast.text || "")}</div>`
      : "";
    const mainCandidateState = candidateAvailability({
      mode,
      sourceText: state.sourceText,
      pending: state.pending || state.candidatePending,
      hasResult: Boolean(result),
      aiAvailable: state.aiAvailable,
      aiAvailableChecked: state.aiAvailableChecked,
    });
    const aiModeEnabled = Boolean(state.aiAvailable);
    const mainCandidateDisabled = !mainCandidateState.enabled || state.candidatePending;
    const showCandidatePane = Boolean(state.candidatePending || (state.candidateItems || []).length);
    const mainDisplayedCandidates = getMainDisplayedCandidates();
    const mainCopyAll = showCandidatePane && mainDisplayedCandidates.length > 0;
    const mainCandidateRows = mainDisplayedCandidates.map((item, index) => `
      <div class="candidate-item">
        <div class="candidate-text" title="${escapeHtml(item)}">${escapeHtml(item)}</div>
        <button class="mini-button candidate-copy" data-action="copy-candidate-main" data-index="${index}" title="复制此候选">${icons.copy}</button>
      </div>`).join("");

    root.innerHTML = `
      <div class="window-shell">
        <section class="window-card ${showResultCard ? "" : "no-result"}">
          <header class="topbar" data-drag-handle="main-topbar">
            <div class="drag-row"><span class="app-badge">${brandIcon("app-badge-icon", state.branding?.appIconUrl)}<span>${escapeHtml(state.appTitle || "WordPack")}</span></span></div>
            <div class="toolbar">
              <button class="icon-button" data-action="open-history" aria-label="历史">${icons.history}</button>
              <button class="icon-button" data-action="open-settings" aria-label="设置">${icons.settings}</button>
              <button class="icon-button" data-action="close-app" aria-label="关闭">${icons.close}</button>
            </div>
          </header>
          <div class="segmented">
            <div class="seg-track">
              <button class="seg-btn ${mode === "argos" ? "active" : ""} ${argosModeAvailability.available ? "" : "disabled"}" data-action="set-mode" data-mode="argos" aria-disabled="${argosModeAvailability.available ? "false" : "true"}" ${argosModeAvailability.available ? "" : `data-tooltip="${escapeHtml(argosModeAvailability.tip)}"`}>${icons.book}<span>词典翻译</span></button>
              <button class="seg-btn ${mode === "ai" ? "active" : ""} ${aiModeEnabled ? "" : "disabled"}" data-action="set-mode" data-mode="ai" aria-disabled="${aiModeAvailability.available ? "false" : "true"}" ${aiModeAvailability.available ? "" : `data-tooltip="${escapeHtml(aiModeAvailability.tip)}"`}>${icons.robot}<span>AI 翻译</span></button>
            </div>
          </div>
          <section class="card input-card">
            <div class="card-head">
              <div class="card-title">输入内容</div>
              <button class="dir-button" data-action="cycle-direction">${escapeHtml(state.ui.direction || "方向: 自动")}</button>
            </div>
            <div class="input-shell">
              <textarea id="sourceText" class="source-textarea" spellcheck="false" placeholder="输入或粘贴待翻译文本"></textarea>
            </div>
          </section>
          <button class="primary-button" data-action="translate" ${translateEnabled ? "" : 'disabled data-tooltip="无可用翻译模式，请检查配置"'}>${mode === "ai" ? icons.robot : icons.book}<span>翻译</span></button>
          <section class="card result-card ${showResultCard ? "" : "hidden"}">
            <div class="card-head">
              <div class="card-title">翻译结果</div>
              <div class="result-actions">
                <button class="mini-button ${mainCandidateDisabled ? "disabled" : ""}" id="mainCandidateBtn" data-action="generate-candidates-main">${icons.candidates}</button>
                <button class="mini-button" data-action="open-zoom">${icons.expand}</button>
              </div>
            </div>
            <div class="result-workbench ${showCandidatePane ? "with-candidates" : ""}">
              <div class="result-pane ${mainResultClass}" id="resultPane" data-preserve-scroll="result">
                <div class="result-scroll" id="resultScroll" data-autoscroll="main-result">${mainResultContent}</div>
              </div>
              <aside class="candidate-pane ${showCandidatePane ? "open" : ""}" id="mainCandidatePane">
                <div class="candidate-list">
                  ${mainCandidateRows || `<div class="candidate-placeholder">点击右上角按钮生成候选</div>`}
                  ${state.candidatePending ? `<div class="candidate-placeholder">候选生成中...</div>` : ""}
                </div>
              </aside>
            </div>
          </section>
          <div class="footer-actions">
            <button class="ghost-button" data-action="copy-result" ${mainCopyAll ? 'title="复制全部候选译文" aria-label="复制全部候选译文"' : 'aria-label="复制"'}>${icons.copy}<span>复制</span></button>
            <button class="ghost-button" data-action="clear-all">${icons.trash}<span>清空</span></button>
          </div>
        </section>
      </div>
      <aside class="side-sheet ${state.historyOpen ? "open" : ""}">
        <div class="sheet-backdrop" data-action="close-history"></div>
        <div class="sheet-panel history-sheet-panel">
          <div class="panel-drag-hitbox" data-drag-handle="history-top"></div>
          <div class="sheet-header" data-drag-handle="history-header">
            <div class="sheet-title">历史记录</div>
            <button class="icon-button" data-action="close-history">${icons.close}</button>
          </div>
          <div class="history-toolbar">
            <div class="history-tabs">
              <button class="history-tab ${historyPanel.tab === "recent" ? "active" : ""}" data-action="history-set-tab" data-tab="recent">最近</button>
              <button class="history-tab ${historyPanel.tab === "favorites" ? "active" : ""}" data-action="history-set-tab" data-tab="favorites">收藏</button>
            </div>
            <div class="history-filters">
              <div class="history-search-row">
                <input class="history-search" data-history-field="q" placeholder="搜索原文或译文" value="${escapeHtml(historyPanel.q)}" />
                <button class="icon-button history-search-trigger" data-action="history-search-now" aria-label="立即搜索">${icons.search}</button>
              </div>
              <div class="history-filter-row">
                <label class="history-filter-item">
                  <span class="history-filter-label">模式</span>
                  <span class="history-select-wrap">
                    <select class="history-filter-select" data-history-field="mode">
                      <option value="all" ${historyPanel.mode === "all" ? "selected" : ""}>全部</option>
                      <option value="argos" ${historyPanel.mode === "argos" ? "selected" : ""}>词典</option>
                      <option value="ai" ${historyPanel.mode === "ai" ? "selected" : ""}>AI</option>
                    </select>
                  </span>
                </label>
                <label class="history-filter-item">
                  <span class="history-filter-label">来源</span>
                  <span class="history-select-wrap">
                    <select class="history-filter-select" data-history-field="source_kind">
                      <option value="all" ${historyPanel.source_kind === "all" ? "selected" : ""}>全部</option>
                      <option value="manual" ${historyPanel.source_kind === "manual" ? "selected" : ""}>手动</option>
                      <option value="selection" ${historyPanel.source_kind === "selection" ? "selected" : ""}>划词</option>
                      <option value="screenshot" ${historyPanel.source_kind === "screenshot" ? "selected" : ""}>截图</option>
                    </select>
                  </span>
                </label>
              </div>
              <div class="history-filter-row">
                <label class="history-filter-item">
                  <span class="history-filter-label">方向</span>
                  <span class="history-select-wrap">
                    <select class="history-filter-select" data-history-field="direction">
                      ${historyDirectionOptions.map((opt) => `<option value="${escapeHtml(opt.value)}" ${historyPanel.direction === opt.value ? "selected" : ""}>${escapeHtml(opt.label)}</option>`).join("")}
                    </select>
                  </span>
                </label>
                <label class="history-filter-item">
                  <span class="history-filter-label">时间</span>
                  <span class="history-select-wrap">
                    <select class="history-filter-select" data-history-field="range_days">
                      <option value="0" ${Number(historyPanel.range_days) === 0 ? "selected" : ""}>全部</option>
                      <option value="7" ${Number(historyPanel.range_days) === 7 ? "selected" : ""}>7 天内</option>
                      <option value="30" ${Number(historyPanel.range_days) === 30 ? "selected" : ""}>30 天内</option>
                      <option value="90" ${Number(historyPanel.range_days) === 90 ? "selected" : ""}>90 天内</option>
                    </select>
                  </span>
                </label>
              </div>
            </div>
          </div>
          <div class="history-list" id="historyList" data-preserve-scroll="history">
            ${historyPanel.items.length ? `
              ${historyPanel.items.map((item) => `
                <article class="history-card" data-history-id="${item.id}">
                  <div class="history-meta">
                    <span>${escapeHtml(item.created_at || "")}</span>
                    <span>${escapeHtml(historySourceLabel(item.source_kind))} · ${escapeHtml(historyModeLabel(item.mode))} · 使用 ${Number(item.use_count || 0)} 次</span>
                  </div>
                  <div class="history-text history-source">${escapeHtml((item.source_text || "").slice(0, 180))}</div>
                  <div class="history-text history-result">${escapeHtml((item.result_text || "").slice(0, 180))}</div>
                  <div class="history-actions">
                    <button class="mini-button ${item.favorite ? "active" : ""}" data-action="history-favorite" data-history-id="${item.id}" data-favorite="${item.favorite ? "0" : "1"}">${item.favorite ? icons.favoriteActive : icons.favorite}</button>
                    <button class="mini-button" data-action="history-copy-source" data-history-id="${item.id}">${icons.copy}</button>
                    <button class="mini-button" data-action="history-copy-result" data-history-id="${item.id}">${icons.clipboard}</button>
                    <button class="mini-button" data-action="history-refill" data-history-id="${item.id}">${icons.expand}</button>
                  </div>
                </article>`).join("")}
              ${historyPanel.has_more ? `<button class="ghost-button history-load-more" data-action="history-load-more" ${historyPanel.loading ? "disabled" : ""}>${historyPanel.loading ? "加载中..." : "加载更多（50条）"}</button>` : ""}
            ` : `<div class="notice">${historyPanel.loading ? "加载中..." : "暂无历史记录"}</div>`}
          </div>
          <div class="settings-actions">
            <button class="ghost-button" data-action="clear-history">${icons.trash}<span>清空历史</span></button>
          </div>
        </div>
      </aside>      <aside class="side-sheet ${state.settingsOpen ? "open" : ""}">
        <div class="sheet-backdrop" data-action="close-settings"></div>
        <div class="sheet-panel settings-sheet-panel">
          <div class="panel-drag-hitbox" data-drag-handle="settings-top"></div>
          <div class="sheet-header" data-drag-handle="settings-header">
            <div class="sheet-title">设置</div>
            <button class="icon-button" data-action="close-settings">${icons.close}</button>
          </div>
          <div class="settings-scroll" id="settingsScroll" data-preserve-scroll="settings">
            ${notice}
            <section class="setting-group">
              <div class="setting-title">外观</div>
              <div class="field">
                <label>主题</label>
                <div class="theme-switch">
                  <div class="theme-track">
                    <button class="theme-btn ${((draft.theme_mode || "system") === "system") ? "active" : ""}" data-action="set-theme-draft" data-theme="system">跟随系统</button>
                    <button class="theme-btn ${draft.theme_mode === "light" ? "active" : ""}" data-action="set-theme-draft" data-theme="light">亮色</button>
                    <button class="theme-btn ${draft.theme_mode === "dark" ? "active" : ""}" data-action="set-theme-draft" data-theme="dark">暗色</button>
                  </div>
                </div>
              </div>
            </section>
            <section class="setting-group">
              <div class="setting-title">AI 配置</div>
              <div class="field"><label>Base URL</label><input spellcheck="false" data-field="openai.base_url" value="${escapeHtml(draft.openai?.base_url || "")}" /></div>
              <div class="field"><label>API Key</label><input type="password" autocomplete="off" spellcheck="false" data-field="openai.api_key" value="${escapeHtml(draft.openai?.api_key || "")}" /></div>
              <div class="field"><label>Model</label><input spellcheck="false" data-field="openai.model" value="${escapeHtml(draft.openai?.model || "")}" /></div>
              <div class="field"><label>Timeout(s)</label><input type="number" min="5" step="1" spellcheck="false" data-field="openai.timeout_sec" value="${escapeHtml(draft.openai?.timeout_sec ?? 60)}" /></div>
              <div class="settings-actions">
                <button class="ghost-button" data-action="ollama-defaults">${icons.robot}<span>Ollama 默认</span></button>
                <button class="ghost-button ai-test-button ${state.testingAi ? "testing" : ""} ${state.aiTestState}" data-action="test-ai" ${state.testingAi ? "disabled" : ""}>${aiTestIcon}<span>${state.testingAi ? "测试中..." : "测试连接"}</span></button>
              </div>
              <div class="ai-status-inline">AI 状态：${escapeHtml(aiStatusText)} · 最近检测：${escapeHtml(aiStatusTime)}</div>
            </section>
            <section class="setting-group">
              <div class="setting-title">Argos 模型</div>
              <div class="field">
                <label>默认方向</label>
                ${choiceGroupMarkup("offline.preferred_direction", draft.offline?.preferred_direction || "auto", directionOptions)}
              </div>
              <div class="notice ${argosNoticeClass}">${argosNoticeHtml}</div>
              <div class="settings-actions">
                 <button class="ghost-button" data-action="import-offline-model">${icons.book}<span>导入 Argos 模型</span></button>
              </div>
            </section>
            <section class="setting-group">
              <div class="setting-title">历史设置</div>
              <div class="field">
                <label>历史保留时长</label>
                ${choiceGroupMarkup("history.retention_days", String(draft.history?.retention_days ?? 30), [
                  { value: "7", label: "7 天" },
                  { value: "30", label: "30 天" },
                  { value: "90", label: "90 天" },
                ])}
                <small>超出保留时长的历史会自动清理。</small>
              </div>
              <div class="settings-actions">
                <button class="ghost-button" data-action="clear-history-from-settings">${icons.trash}<span>一键清理历史</span></button>
              </div>
            </section>
            <section class="setting-group">
              <div class="setting-title">操作设置</div>
              <div class="field">
                <label class="toggle-row"><input type="checkbox" data-field="interaction.selection_enabled" ${selectionEnabled ? "checked" : ""}/>启用划词</label>
              </div>
              ${selectionEnabled ? `
                <div class="field-row">
                  <div class="field">
                    <label>划词触发模式</label>
                    ${choiceGroupMarkup("interaction.selection_trigger_mode", selectionTriggerMode, selectionModeOptions)}
                  </div>
                  ${selectionTriggerMode === "icon" ? `
                  <div class="field">
                    <label>图标触发方式</label>
                    ${choiceGroupMarkup("interaction.selection_icon_trigger", draft.interaction?.selection_icon_trigger || "click", iconTriggerOptions)}
                  </div>
                  ` : ""}
                </div>
                ${selectionTriggerMode === "icon" ? `
                  <div class="field">
                    <label>图标延时(ms)</label>
                    <input type="number" min="300" max="5000" step="100" data-field="interaction.selection_icon_delay_ms" value="${escapeHtml(draft.interaction?.selection_icon_delay_ms ?? 1500)}" />
                  </div>
                ` : ""}
              ` : ""}
              <div class="field">
                <label class="toggle-row"><input type="checkbox" data-field="interaction.screenshot_enabled" ${screenshotEnabled ? "checked" : ""}/>启用截图翻译</label>
              </div>
              ${screenshotEnabled ? `
                <div class="field">
                  <label>截图翻译快捷键</label>
                  <input class="shortcut-input" data-shortcut-field="interaction.screenshot_hotkey" value="${escapeHtml(screenshotHotkey)}" placeholder="按下新的快捷键组合" readonly />
                  <small>按下新的组合键即可保存，Backspace / Delete / Esc 可清空。</small>
                </div>
              ` : ""}
            </section>
          </div>
        </div>
      </aside>
      <section class="zoom-modal ${state.zoomOpen ? "open" : ""}">
        <div class="modal-backdrop" data-action="close-zoom"></div>
        <div class="modal-panel">
          <div class="modal-header">
            <div class="modal-title">放大查看</div>
            <button class="icon-button" data-action="close-zoom">${icons.close}</button>
          </div>
          <div class="zoom-shell" id="zoomShell" data-preserve-scroll="zoom-shell" data-autoscroll="zoom-shell">
            <div class="zoom-grid">
              <section class="zoom-section">
                <div class="card-title">原文</div>
                <div class="zoom-text" id="zoomSource">${escapeHtml(state.zoomPayload?.sourceText || state.sourceText)}</div>
              </section>
              <section class="zoom-section">
                <div class="card-title">译文</div>
                <div class="zoom-text" id="zoomResult">${escapeHtml(state.zoomPayload?.resultText || state.resultText)}</div>
              </section>
            </div>
          </div>
        </div>
      </section>
      ${toast}`;

    const textarea = document.getElementById("sourceText");
    if (textarea && textarea.value !== state.sourceText) {
      textarea.value = state.sourceText;
    }
  }

  function renderBubble() {
    const bubble = state.bubble || {
      source_text: "",
      result_text: "",
      pending: false,
      pinned: false,
      action: "划词翻译",
      candidate_pending: false,
      candidate_items: [],
    };
    const mode = bubble.mode || "argos";
    const bubbleResultClass = bubble.pending ? ((bubble.result_text || "") ? "pending-streaming" : "pending-waiting") : "";
    const bubbleResultContent = bubble.pending && !bubble.result_text ? skeletonMarkup("bubble") : escapeHtml(bubble.result_text || "暂无结果");
    const bubbleCandidateItems = Array.isArray(bubble.candidate_items) ? bubble.candidate_items : [];
    const bubbleCandidateState = candidateAvailability({
      mode,
      sourceText: bubble.source_text,
      pending: Boolean(bubble.pending || bubble.candidate_pending),
      hasResult: Boolean((bubble.result_text || "").trim()),
      aiAvailable: state.aiAvailable,
      aiAvailableChecked: state.aiAvailableChecked,
    });
    const bubbleModeAiEnabled = Boolean(state.aiAvailable);
    const bubbleArgosModeAvailability = modeAvailability("argos");
    const bubbleAiModeAvailability = modeAvailability("ai");
    const bubbleCandidateDisabled = !bubbleCandidateState.enabled || bubble.candidate_pending;
    const showBubbleCandidatePane = Boolean(state.bubbleCandidatePaneOpen || bubble.candidate_pending || bubbleCandidateItems.length);
    const bubbleDisplayedCandidates = (String(bubble.result_text || "").trim() ? [String(bubble.result_text || "").trim(), ...dedupeCandidateItems(bubbleCandidateItems)] : dedupeCandidateItems(bubbleCandidateItems)).slice(0, 4);
    const bubbleCandidateRows = bubbleDisplayedCandidates.map((item, index) => `
      <div class="bubble-candidate-item">
        <div class="bubble-candidate-text" title="${escapeHtml(item)}">${escapeHtml(item)}</div>
        <button class="mini-button bubble-candidate-copy" data-action="copy-candidate-bubble" data-index="${index}" title="复制此候选">${icons.copy}</button>
      </div>
    `).join("");
    root.innerHTML = `
      <div class="bubble-shell">
        <section class="bubble-card">
          <div class="panel-drag-hitbox" data-drag-handle="bubble-top"></div>
          <header class="bubble-header" data-drag-handle="bubble-header">
            <button class="icon-button bubble-pin bubble-pin-corner ${bubble.pinned ? "active" : ""}" data-action="toggle-pin" aria-label="置顶">${icons.pin}</button>
            <button class="icon-button bubble-candidate-toggle ${bubbleCandidateDisabled ? "disabled" : ""}" data-action="generate-candidates-bubble">${icons.candidates}</button>
            <div class="bubble-header-spacer" aria-hidden="true"></div>
            <div class="bubble-mode-switch">
              <button class="mode-chip ${mode === "argos" ? "active" : ""} ${bubbleArgosModeAvailability.available ? "" : "disabled"}" data-action="set-mode-bubble" data-mode="argos" aria-label="词典翻译" aria-disabled="${bubbleArgosModeAvailability.available ? "false" : "true"}" ${bubbleArgosModeAvailability.available ? "" : `data-tooltip="${escapeHtml(bubbleArgosModeAvailability.tip)}"`}>${icons.book}</button>
              <button class="mode-chip ${mode === "ai" ? "active" : ""} ${bubbleModeAiEnabled ? "" : "disabled"}" data-action="set-mode-bubble" data-mode="ai" aria-label="AI翻译" aria-disabled="${bubbleAiModeAvailability.available ? "false" : "true"}" ${bubbleAiModeAvailability.available ? "" : `data-tooltip="${escapeHtml(bubbleAiModeAvailability.tip)}"`}>${icons.robot}</button>
            </div>
            <button class="icon-button bubble-close" data-action="close-app" aria-label="关闭">${icons.close}</button>
          </header>
          <div class="bubble-content bubble-content-single">
            <div class="bubble-workbench ${showBubbleCandidatePane ? "with-candidates" : ""}">
              <div class="bubble-stack bubble-stack-result bubble-stack-only">
                <div class="bubble-label bubble-result-label"></div>
                <div class="bubble-result ${bubbleResultClass}">
                  <div class="bubble-result-scroll" data-autoscroll="bubble-result">${bubbleResultContent}</div>
                </div>
              </div>
              <aside class="bubble-candidate-pane ${showBubbleCandidatePane ? "open" : ""}">
                <div class="bubble-candidate-list">
                  ${bubbleCandidateRows || `<div class="bubble-candidate-placeholder">点击顶部按钮生成候选</div>`}
                  ${bubble.candidate_pending ? `<div class="bubble-candidate-placeholder">候选生成中...</div>` : ""}
                </div>
              </aside>
            </div>
          </div>
          <div class="bubble-actions">
            <button class="icon-button bubble-action-icon" data-action="copy-result" aria-label="复制">${icons.copy}</button>
            <button class="icon-button bubble-action-icon" data-action="open-zoom-bubble" aria-label="放大查看">${icons.expand}</button>
          </div>
        </section>
      </div>`;
  }

  function patchMainDynamic() {
    if (state.view !== "main") return false;
    const textarea = document.getElementById("sourceText");
    const resultPane = document.getElementById("resultPane");
    const resultScroll = document.getElementById("resultScroll");
    const statusText = document.getElementById("statusText");
    if (!textarea || !resultPane || !resultScroll) return false;

    if (document.activeElement !== textarea && textarea.value !== state.sourceText) {
      textarea.value = state.sourceText;
    }

    updateResultCardVisibility();
    syncMainCompact();
    resultScroll.innerHTML = state.pending && !state.resultText
      ? skeletonMarkup("main")
      : escapeHtml(state.resultText || "");
    resultPane.classList.toggle("pending-streaming", Boolean(state.pending && state.resultText));
    resultPane.classList.toggle("pending-waiting", Boolean(state.pending && !state.resultText));
    if (statusText) {
      statusText.textContent = state.ui.status || "就绪";
    }
    applyAutoScroll("main-result");

    const mode = state.config?.translation_mode || "argos";
    const candidateState = candidateAvailability({
      mode,
      sourceText: state.sourceText,
      pending: state.pending || state.candidatePending,
      hasResult: Boolean(state.resultText),
      aiAvailable: state.aiAvailable,
      aiAvailableChecked: state.aiAvailableChecked,
    });
    const candidateButton = document.getElementById("mainCandidateBtn");
    const candidatePane = document.getElementById("mainCandidatePane");
    const candidateList = candidatePane?.querySelector(".candidate-list");
    const showCandidatePane = Boolean(state.candidatePending || (state.candidateItems || []).length);
    candidatePane?.classList.toggle("open", showCandidatePane);
    candidatePane?.parentElement?.classList.toggle("with-candidates", showCandidatePane);
    const displayedMainCandidates = getMainDisplayedCandidates();
    const mainCopyAll = showCandidatePane && displayedMainCandidates.length > 0;

    if (candidateButton) {
      const disabled = !candidateState.enabled || state.candidatePending;
      candidateButton.classList.toggle("disabled", disabled);
      candidateButton.removeAttribute("title");
    }
    const mainModeAiButton = document.querySelector('.seg-btn[data-mode="ai"]');
    const mainModeArgosButton = document.querySelector('.seg-btn[data-mode="argos"]');
    const argosEnabled = modeAvailability("argos").available;
    const aiEnabled = modeAvailability("ai").available;
    if (mainModeArgosButton) {
      mainModeArgosButton.classList.toggle("disabled", !argosEnabled);
      if (argosEnabled) {
        mainModeArgosButton.removeAttribute("title");
        delete mainModeArgosButton.dataset.tooltip;
        mainModeArgosButton.setAttribute("aria-disabled", "false");
      } else {
        mainModeArgosButton.removeAttribute("title");
        mainModeArgosButton.dataset.tooltip = "模式不可用，请检查配置";
        mainModeArgosButton.setAttribute("aria-disabled", "true");
      }
    }
    if (mainModeAiButton) {
      mainModeAiButton.classList.toggle("disabled", !aiEnabled);
      if (aiEnabled) {
        mainModeAiButton.removeAttribute("title");
        delete mainModeAiButton.dataset.tooltip;
        mainModeAiButton.setAttribute("aria-disabled", "false");
      } else {
        mainModeAiButton.removeAttribute("title");
        mainModeAiButton.dataset.tooltip = "模式不可用，请检查配置";
        mainModeAiButton.setAttribute("aria-disabled", "true");
      }
    }
    const translateButton = document.querySelector('.primary-button[data-action="translate"]');
    if (translateButton instanceof HTMLButtonElement) {
      const enabled = anyModeAvailable();
      translateButton.disabled = !enabled;
      if (enabled) {
        translateButton.removeAttribute("title");
        delete translateButton.dataset.tooltip;
      } else {
        translateButton.removeAttribute("title");
        translateButton.dataset.tooltip = "无可用翻译模式，请检查配置";
      }
    }
    if (candidateList) {
      if (displayedMainCandidates.length) {
        candidateList.innerHTML = displayedMainCandidates.map((item, index) => `
          <div class="candidate-item">
            <div class="candidate-text" title="${escapeHtml(item)}">${escapeHtml(item)}</div>
            <button class="mini-button candidate-copy" data-action="copy-candidate-main" data-index="${index}" title="复制此候选">${icons.copy}</button>
          </div>
        `).join("");
        if (state.candidatePending) {
          candidateList.innerHTML += `<div class="candidate-placeholder">候选生成中...</div>`;
        }
      } else {
        candidateList.innerHTML = state.candidatePending
          ? `<div class="candidate-placeholder">候选生成中...</div>`
          : `<div class="candidate-placeholder">点击右上角按钮生成候选</div>`;
      }
    }
    const footerCopy = document.querySelector('.footer-actions [data-action="copy-result"]');
    if (footerCopy) {
      const hint = mainCopyAll ? "复制全部候选译文" : "";
      if (hint) {
        footerCopy.title = hint;
        footerCopy.setAttribute("aria-label", hint);
      } else {
        footerCopy.removeAttribute("title");
        footerCopy.setAttribute("aria-label", "复制");
      }
    }

    const zoomSource = document.getElementById("zoomSource");
    const zoomResult = document.getElementById("zoomResult");
    if (zoomSource) zoomSource.textContent = state.zoomPayload?.sourceText || state.sourceText;
    if (zoomResult) zoomResult.textContent = state.zoomPayload?.resultText || state.resultText;
    applyAutoScroll("zoom-shell");
    return true;
  }

  function patchBubbleDynamic() {
    if (state.view !== "bubble") return false;
    const label = document.querySelector(".bubble-result-label");
    const result = document.querySelector(".bubble-result");
    const pin = document.querySelector(".bubble-pin");
    const modeArgos = document.querySelector('.bubble-mode-switch [data-mode="argos"]');
    const modeAi = document.querySelector('.bubble-mode-switch [data-mode="ai"]');
    if (!label || !result || !pin || !state.bubble) return false;

    label.textContent = "";
    const resultScroll = result.querySelector(".bubble-result-scroll");
    if (!resultScroll) return false;
    resultScroll.innerHTML = state.bubble.pending && !state.bubble.result_text
      ? skeletonMarkup("bubble")
      : escapeHtml(state.bubble.result_text || "暂无结果");
    result.classList.toggle("pending-streaming", Boolean(state.bubble.pending && state.bubble.result_text));
    result.classList.toggle("pending-waiting", Boolean(state.bubble.pending && !state.bubble.result_text));
    pin.classList.toggle("active", Boolean(state.bubble.pinned));
    const activeMode = state.bubble.mode || "argos";
    if (modeArgos) {
      const argosEnabled = modeAvailability("argos").available;
      modeArgos.classList.toggle("active", activeMode === "argos");
      modeArgos.classList.toggle("disabled", !argosEnabled);
      if (argosEnabled) {
        modeArgos.removeAttribute("title");
        delete modeArgos.dataset.tooltip;
        modeArgos.setAttribute("aria-disabled", "false");
      } else {
        modeArgos.removeAttribute("title");
        modeArgos.dataset.tooltip = "模式不可用，请检查配置";
        modeArgos.setAttribute("aria-disabled", "true");
      }
    }
    if (modeAi) {
      const aiEnabled = modeAvailability("ai").available;
      modeAi.classList.toggle("active", activeMode === "ai");
      modeAi.classList.toggle("disabled", !aiEnabled);
      if (aiEnabled) {
        modeAi.removeAttribute("title");
        delete modeAi.dataset.tooltip;
        modeAi.setAttribute("aria-disabled", "false");
      } else {
        modeAi.removeAttribute("title");
        modeAi.dataset.tooltip = "模式不可用，请检查配置";
        modeAi.setAttribute("aria-disabled", "true");
      }
    }

    const candidateBtn = document.querySelector('[data-action="generate-candidates-bubble"]');
    const candidatePane = document.querySelector(".bubble-candidate-pane");
    const candidateList = document.querySelector(".bubble-candidate-list");
    const bubbleCandidateState = candidateAvailability({
      mode: activeMode,
      sourceText: state.bubble?.source_text || "",
      pending: Boolean(state.bubble?.pending || state.bubble?.candidate_pending),
      hasResult: Boolean((state.bubble?.result_text || "").trim()),
      aiAvailable: state.aiAvailable,
      aiAvailableChecked: state.aiAvailableChecked,
    });
    const bubbleCandidateDisabled = !bubbleCandidateState.enabled || Boolean(state.bubble?.candidate_pending);
    if (candidateBtn) {
      candidateBtn.classList.toggle("disabled", bubbleCandidateDisabled);
      candidateBtn.removeAttribute("title");
    }
    const bubbleDisplayedCandidates = getBubbleDisplayedCandidates();
    const showBubbleCandidatePane = Boolean(
      state.bubbleCandidatePaneOpen
      || state.bubble?.candidate_pending
      || (state.bubble?.candidate_items || []).length
    );
    if (showBubbleCandidatePane && !candidatePane) {
      return false;
    }
    candidatePane?.classList.toggle("open", showBubbleCandidatePane);
    candidatePane?.parentElement?.classList.toggle("with-candidates", showBubbleCandidatePane);
    if (candidateList) {
      if (bubbleDisplayedCandidates.length) {
        candidateList.innerHTML = bubbleDisplayedCandidates.map((item, index) => `
          <div class="bubble-candidate-item">
            <div class="bubble-candidate-text" title="${escapeHtml(item)}">${escapeHtml(item)}</div>
            <button class="mini-button bubble-candidate-copy" data-action="copy-candidate-bubble" data-index="${index}" title="复制此候选">${icons.copy}</button>
          </div>
        `).join("");
        if (state.bubble?.candidate_pending) {
          candidateList.innerHTML += `<div class="bubble-candidate-placeholder">候选生成中...</div>`;
        }
      } else {
        candidateList.innerHTML = state.bubble?.candidate_pending
          ? `<div class="bubble-candidate-placeholder">候选生成中...</div>`
          : `<div class="bubble-candidate-placeholder">点击顶部按钮生成候选</div>`;
      }
    }
    applyAutoScroll("bubble-result");
    return true;
  }

  function captureFocusState() {
    const active = document.activeElement;
    if (!(active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement)) {
      return null;
    }
    let selector = "";
    if (active.id) {
      selector = `#${active.id}`;
    } else if (active.dataset?.field) {
      selector = `[data-field="${active.dataset.field}"]`;
    } else if (active.dataset?.shortcutField) {
      selector = `[data-shortcut-field="${active.dataset.shortcutField}"]`;
    } else if (active.dataset?.historyField) {
      selector = `[data-history-field="${active.dataset.historyField}"]`;
    } else {
      return null;
    }
    const canSelection = active instanceof HTMLTextAreaElement
      || (active instanceof HTMLInputElement && !["checkbox", "radio", "button", "submit", "reset", "file"].includes(active.type));
    return {
      selector,
      selectionStart: canSelection ? active.selectionStart : null,
      selectionEnd: canSelection ? active.selectionEnd : null,
    };
  }

  function restoreFocusState(focusState) {
    if (!focusState || !focusState.selector) return;
    const node = document.querySelector(focusState.selector);
    if (!(node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement)) {
      return;
    }
    node.focus();
    if (focusState.selectionStart == null || focusState.selectionEnd == null) return;
    try {
      node.setSelectionRange(focusState.selectionStart, focusState.selectionEnd);
    } catch (_) {
      // number/password-like inputs may not support selection range in all engines.
    }
  }

  async function handleClick(event) {
    const actionTarget = event.target.closest("[data-action]");
    if (!actionTarget) return;

    const action = actionTarget.dataset.action;
    if (["open-history", "open-settings", "close-settings", "close-history", "open-zoom", "close-zoom"].includes(action)) {
      event.preventDefault();
    }

    switch (action) {
      case "open-history":
        await openHistoryPanel();
        break;
      case "close-history":
        clearSideSheetOpenTimer();
        state.historyOpen = false;
        rerender();
        break;
      case "open-settings":
        await openSettingsPanel();
        break;
      case "close-settings":
        clearSideSheetOpenTimer();
        state.settingsOpen = false;
        rerender();
        break;
      case "open-zoom":
        scrollFollowState["zoom-shell"] = true;
        await apiCall("open_zoom_panel");
        state.zoomPayload = { sourceText: state.sourceText, resultText: state.resultText, origin: "main" };
        state.zoomOpen = true;
        rerender();
        break;
      case "close-zoom":
        await apiCall("close_zoom_panel");
        state.zoomOpen = false;
        rerender();
        break;
      case "set-mode":
        {
          const nextMode = String(actionTarget.dataset.mode || "").trim();
          const currentMode = state.config?.translation_mode || state.ui?.translation_mode || "";
          if (!nextMode || currentMode === nextMode) {
            break;
          }
          if (nextMode === "argos" && !modeAvailability("argos").available) {
            break;
          }
          if (nextMode === "ai" && !state.aiAvailable) {
            break;
          }
          if (nextMode !== "ai") {
            state.candidatePending = false;
            state.candidateItems = [];
            state.candidateReqId = 0;
            state.bubbleCandidatePaneOpen = false;
            if (state.bubble) {
              state.bubble.candidate_pending = false;
              state.bubble.candidate_items = [];
            }
          }
          const resp = await apiCall("set_mode", nextMode);
          if (resp && resp.ok === false) {
            showToast("warning", resp.message || "模式切换失败");
          }
        }
        break;
      case "set-mode-bubble": {
        const nextMode = String(actionTarget.dataset.mode || "").trim();
        if (!nextMode || (nextMode !== "argos" && nextMode !== "ai")) {
          break;
        }
        const currentMode = state.config?.translation_mode || state.ui?.translation_mode || "";
        if (currentMode === nextMode) {
          break;
        }
        if (nextMode === "argos" && !modeAvailability("argos").available) {
          break;
        }
        if (nextMode === "ai" && !state.aiAvailable) {
          break;
        }
        const previousBubbleMode = state.bubble?.mode || currentMode || "argos";
        if (state.bubble) {
          state.bubble.mode = nextMode;
          state.bubble.pending = true;
          state.bubble.result_text = "";
          state.bubble.candidate_pending = false;
          state.bubble.candidate_items = [];
        }
        if (nextMode !== "ai") {
          state.bubbleCandidatePaneOpen = false;
        }
        if (state.config) {
          state.config.translation_mode = nextMode;
        }
        state.ui.translation_mode = nextMode;
        if (!patchBubbleDynamic()) rerender();
        const modeResp = await apiCall("set_mode", nextMode);
        if (modeResp && modeResp.ok === false) {
          showToast("warning", modeResp.message || "模式切换失败");
          if (state.bubble) {
            state.bubble.mode = previousBubbleMode;
            state.bubble.pending = false;
          }
          if (state.config) state.config.translation_mode = previousBubbleMode;
          state.ui.translation_mode = previousBubbleMode;
          if (!patchBubbleDynamic()) rerender();
          break;
        }
        const sourceText = String(state.bubble?.source_text || "").trim();
        if (sourceText) {
          scrollFollowState["bubble-result"] = true;
            await apiCall("translate", sourceText, state.bubble?.action || "划词翻译");
        } else {
          if (state.bubble) {
            state.bubble.pending = false;
          }
          showToast("warning", "当前无可翻译文本");
          if (!patchBubbleDynamic()) rerender();
        }
        break;
      }
      case "set-theme-draft":
        if (!state.settingsDraft) state.settingsDraft = clone(state.config || {});
        state.settingsDraft.theme_mode = actionTarget.dataset.theme || "system";
        scheduleSettingsSave(true);
        rerender();
        break;
      case "set-setting-choice":
        if (!state.settingsDraft) state.settingsDraft = clone(state.config || {});
        setValue(state.settingsDraft, actionTarget.dataset.field || "", actionTarget.dataset.value || "");
        scheduleSettingsSave(true);
        rerender();
        break;
      case "cycle-direction":
        await apiCall("cycle_direction");
        break;
      case "translate":
        if (!anyModeAvailable()) {
          break;
        }
        {
          const currentMode = String(state.config?.translation_mode || state.ui?.translation_mode || "argos").toLowerCase();
          if (!modeAvailability(currentMode).available) {
            const fallbackMode = modeAvailability("ai").available ? "ai" : "argos";
            if (fallbackMode !== currentMode) {
              const modeResp = await apiCall("set_mode", fallbackMode);
              if (modeResp && modeResp.ok === false) {
                break;
              }
              if (state.config) state.config.translation_mode = fallbackMode;
              state.ui.translation_mode = fallbackMode;
            }
          }
        }
        scrollFollowState["main-result"] = true;
        await apiCall("translate", state.sourceText, "翻译");
        break;
      case "generate-candidates-main": {
        const availability = candidateAvailability({
          mode: state.config?.translation_mode || "argos",
          sourceText: state.sourceText,
          pending: state.pending || state.candidatePending,
          hasResult: Boolean(state.resultText),
          aiAvailable: state.aiAvailable,
          aiAvailableChecked: state.aiAvailableChecked,
        });
        if (!availability.enabled) {
          showToast("warning", availability.tip || "候选生成功能不可用");
          break;
        }
        if ((state.config?.translation_mode || "argos") !== "ai") {
          const modeResp = await apiCall("set_mode", "ai");
          if (modeResp && modeResp.ok === false) {
            showToast("warning", modeResp.message || "模式切换失败");
            break;
          }
          if (state.config) state.config.translation_mode = "ai";
          state.ui.translation_mode = "ai";
        }
        if (state.candidatePending) break;
        state.candidatePending = true;
        state.candidateItems = [];
        rerender();
        const response = await apiCall("generate_multi_candidates", state.sourceText || "", state.resultText || "");
        if (response && response.ok === false) {
          state.candidatePending = false;
          showToast("warning", response.message || "候选生成功能不可用");
          rerender();
        }
        break;
      }
      case "generate-candidates-bubble": {
        state.bubbleCandidatePaneOpen = true;
        const availability = candidateAvailability({
          mode: state.bubble?.mode || state.config?.translation_mode || state.ui?.translation_mode || "argos",
          sourceText: String(state.bubble?.source_text || "").trim(),
          pending: Boolean(state.bubble?.pending || state.bubble?.candidate_pending),
          hasResult: Boolean((state.bubble?.result_text || "").trim()),
          aiAvailable: state.aiAvailable,
          aiAvailableChecked: state.aiAvailableChecked,
        });
        if (!availability.enabled) {
          showToast("warning", availability.tip || "候选生成功能不可用");
          break;
        }
        if ((state.config?.translation_mode || state.ui?.translation_mode || "argos") !== "ai") {
          const modeResp = await apiCall("set_mode", "ai");
          if (modeResp && modeResp.ok === false) {
            showToast("warning", modeResp.message || "模式切换失败");
            if (state.bubble) {
              state.bubble.candidate_pending = false;
            }
            if (!patchBubbleDynamic()) rerender();
            break;
          }
          if (state.config) state.config.translation_mode = "ai";
          state.ui.translation_mode = "ai";
          if (state.bubble) state.bubble.mode = "ai";
        }
        if (state.bubble) {
          state.bubble.candidate_pending = true;
          state.bubble.candidate_items = [];
        }
        if (!patchBubbleDynamic()) rerender();
        const sourceText = String(state.bubble?.source_text || "").trim();
        const response = await apiCall("generate_multi_candidates", sourceText, state.bubble?.result_text || "");
        if (response && response.ok === false) {
          if (state.bubble) {
            state.bubble.candidate_pending = false;
          }
          showToast("warning", response.message || "候选生成功能不可用");
          if (!patchBubbleDynamic()) rerender();
        }
        break;
      }
      case "copy-candidate-main": {
        const index = Number(actionTarget.dataset.index || "-1");
        const items = getMainDisplayedCandidates();
        if (index < 0 || index >= items.length) break;
        await apiCall("copy_text", items[index] || "");
        break;
      }
      case "copy-candidate-bubble": {
        const index = Number(actionTarget.dataset.index || "-1");
        const items = getBubbleDisplayedCandidates();
        if (index < 0 || index >= items.length) break;
        await apiCall("copy_text", items[index] || "");
        break;
      }
      case "copy-result":
        if (state.view === "bubble") {
          const bubbleItems = getBubbleDisplayedCandidates();
          const bubbleCandidateOpen = Boolean(state.bubble?.candidate_pending || (state.bubble?.candidate_items || []).length);
          if (bubbleCandidateOpen && bubbleItems.length > 0) {
            await apiCall("copy_text", formatAllCandidates(bubbleItems));
          } else {
            await apiCall("copy_text", state.bubble?.result_text || "");
          }
        } else {
          const mainItems = getMainDisplayedCandidates();
          const mainCandidateOpen = Boolean(state.candidatePending || (state.candidateItems || []).length);
          if (mainCandidateOpen && mainItems.length > 0) {
            await apiCall("copy_text", formatAllCandidates(mainItems));
          } else {
            await apiCall("copy_text", state.resultText);
          }
        }
        break;
      case "clear-all":
        state.mainReqId = 0;
        state.sourceText = "";
        state.resultText = "";
        state.candidatePending = false;
        state.candidateItems = [];
        state.candidateReqId = 0;
        state.pending = false;
        rerender();
        void apiCall("cancel_translation");
        break;
      case "close-app":
        if (state.view === "bubble") {
          state.bubbleCandidatePaneOpen = false;
        }
        await apiCall("close_window");
        break;
      case "test-ai":
        state.testingAi = true;
        state.aiTestState = "idle";
        rerender();
        await apiCall("test_ai_connection");
        break;
      case "import-offline-model":
        await apiCall("import_offline_model");
        break;
      case "ollama-defaults":
        if (!state.settingsDraft) state.settingsDraft = clone(state.config || {});
        state.settingsDraft.openai.base_url = "http://127.0.0.1:11434/v1";
        state.settingsDraft.openai.api_key = "ollama";
        if (!state.settingsDraft.openai.model) state.settingsDraft.openai.model = "qwen2.5:7b";
        scheduleSettingsSave(true);
        rerender();
        break;
      case "clear-history":
        if (window.confirm("确定清空本地历史记录吗？")) {
          await apiCall("clear_history", { scope: "all" });
          await loadHistory(true);
        }
        break;
      case "clear-history-from-settings":
        if (window.confirm("确定清空本地历史记录吗？")) {
          await apiCall("clear_history", { scope: "all" });
          await loadHistory(true);
        }
        break;
      case "history-set-tab":
        state.historyPanel.tab = actionTarget.dataset.tab === "favorites" ? "favorites" : "recent";
        state.historyPanel.offset = 0;
        await loadHistory(true);
        break;
      case "history-search-now":
        keepHistorySearchFocus = true;
        scheduleHistorySearch(true);
        break;
      case "history-load-more":
        if (state.historyPanel.has_more && !state.historyPanel.loading) {
          await loadHistory(false);
        }
        break;
      case "history-favorite": {
        const id = Number(actionTarget.dataset.historyId || "0");
        const favorite = String(actionTarget.dataset.favorite || "0") === "1";
        if (id > 0) {
          await apiCall("toggle_history_favorite", id, favorite);
          await loadHistory(true);
        }
        break;
      }
      case "history-copy-source":
      case "history-copy-result":
      case "history-refill": {
        const id = Number(actionTarget.dataset.historyId || "0");
        const item = state.historyPanel.items.find((x) => Number(x.id) === id);
        if (!item) break;
        if (action === "history-copy-source") {
          await apiCall("copy_text", item.source_text || "");
        } else if (action === "history-copy-result") {
          await apiCall("copy_text", item.result_text || "");
        } else {
          state.sourceText = item.source_text || "";
          state.resultText = item.result_text || "";
          state.pending = false;
          state.historyOpen = false;
          await apiCall("use_history_record", id);
          rerender();
        }
        break;
      }
      case "toggle-pin":
        await apiCall("toggle_bubble_pin");
        break;
      case "open-zoom-bubble":
        scrollFollowState["zoom-shell"] = true;
        await apiCall("open_zoom_from_bubble");
        break;
      case "trigger-selection":
        await apiCall("trigger_selection_translate");
        break;
      default:
        break;
    }
  }

  function handleInput(event) {
    const historyField = event.target.dataset.historyField;
    if (historyField) {
      let value = event.target.value;
      if (historyField === "range_days") {
        value = Number(value || 0);
      }
      state.historyPanel[historyField] = value;
      if (historyField === "q") {
        return;
      }
      scheduleHistorySearch(true);
      return;
    }

    if (event.target.id === "sourceText") {
      state.sourceText = event.target.value;
      patchMainDynamic();
      return;
    }
    const field = event.target.dataset.field;
    if (!field) return;

    if (!state.settingsDraft) {
      state.settingsDraft = clone(state.config || {});
    }
    const value = event.target.type === "checkbox" ? event.target.checked : event.target.value;
    setValue(state.settingsDraft, field, value);
    if (state.settingsOpen) {
      const immediate = event.target.type === "checkbox" || event.type === "change";
      scheduleSettingsSave(immediate);
    }
    if (field.startsWith("openai.")) {
      state.aiTestState = "idle";
    }
    if (
      field === "interaction.selection_enabled"
      || field === "interaction.selection_trigger_mode"
      || field === "interaction.screenshot_enabled"
    ) {
      rerender();
    }
  }

  function handleBackendEvent(event, payload) {
    let shouldRerender = true;
    switch (event) {
      case "status":
        state.ui.status = payload.text || "";
        if (patchMainDynamic()) return;
        break;
      case "config-updated":
        if (payload.config) state.config = payload.config;
        if (payload.ui) state.ui = payload.ui;
        if (payload.settings) state.settings = payload.settings;
        if (payload.aiAvailability) {
          state.aiAvailable = Boolean(payload.aiAvailability.available);
          state.aiAvailableChecked = Boolean(payload.aiAvailability.checked);
          state.aiAvailabilityMessage = String(payload.aiAvailability.message || "");
          state.aiAvailabilityCheckedAt = Number(payload.aiAvailability.checkedAt || 0);
        }
        if (state.config?.translation_mode !== "ai") {
          state.candidatePending = false;
          state.candidateItems = [];
          state.candidateReqId = 0;
        }
        state.settingsDraft = clone(state.config || {});
        setTheme(payload.themeMode || payload.settings?.effectiveTheme || "light");
        break;
      case "translation-start":
        state.mainReqId = Number(payload.reqId || 0);
        state.sourceText = payload.sourceText || state.sourceText;
        state.resultText = "";
        state.candidatePending = false;
        state.candidateItems = [];
        state.candidateReqId = 0;
        state.pending = true;
        scrollFollowState["main-result"] = true;
        scrollFollowState["bubble-result"] = true;
        scrollFollowState["zoom-shell"] = true;
        if (state.zoomPayload && state.zoomPayload.origin !== "bubble") {
          state.zoomPayload.sourceText = state.sourceText;
          state.zoomPayload.resultText = "";
        }
        if (patchMainDynamic()) return;
        break;
      case "translation-chunk":
        if (!state.mainReqId || Number(payload.reqId || 0) !== state.mainReqId) {
          break;
        }
        state.sourceText = payload.sourceText || state.sourceText;
        state.resultText = payload.resultText || state.resultText;
        state.pending = true;
        if (state.zoomPayload && state.zoomPayload.origin !== "bubble") {
          state.zoomPayload.sourceText = state.sourceText;
          state.zoomPayload.resultText = state.resultText;
        }
        if (patchMainDynamic()) return;
        break;
      case "translation-done":
        if (!state.mainReqId || Number(payload.reqId || 0) !== state.mainReqId) {
          break;
        }
        state.sourceText = payload.sourceText || state.sourceText;
        state.resultText = payload.resultText || "";
        state.pending = false;
        state.mainReqId = 0;
        state.ui.history = payload.history || state.ui.history;
        if (state.historyOpen) {
          void loadHistory(true);
        }
        if (state.zoomPayload && state.zoomPayload.origin !== "bubble") {
          state.zoomPayload.sourceText = state.sourceText;
          state.zoomPayload.resultText = state.resultText;
        }
        if (!state.historyOpen && !state.settingsOpen && patchMainDynamic()) {
          shouldRerender = false;
        }
        break;
      case "translation-error":
        if (!state.mainReqId || Number(payload.reqId || 0) !== state.mainReqId) {
          break;
        }
        state.pending = false;
        state.mainReqId = 0;
        state.resultText = payload.resultText ? `${payload.resultText}\n\n${payload.message}` : payload.message || "";
        state.candidatePending = false;
        state.candidateItems = [];
        state.candidateReqId = 0;
        if (state.zoomPayload && state.zoomPayload.origin !== "bubble") {
          state.zoomPayload.resultText = state.resultText;
        }
        state.notice = { type: "error", text: payload.message || "翻译失败" };
        break;
      case "translation-cancelled":
        if (!state.mainReqId || Number(payload.reqId || 0) !== state.mainReqId) {
          break;
        }
        state.pending = false;
        state.mainReqId = 0;
        state.candidatePending = false;
        state.candidateReqId = 0;
        if (typeof payload.resultText === "string") {
          state.resultText = payload.resultText;
        }
        if (patchMainDynamic()) return;
        break;
      case "multi-candidates-start":
        state.candidateReqId = Number(payload.reqId || 0);
        state.candidatePending = true;
        state.candidateItems = [];
        if (patchMainDynamic()) return;
        break;
      case "multi-candidates-done":
        if (state.candidateReqId && Number(payload.reqId || 0) !== state.candidateReqId) {
          break;
        }
        state.candidatePending = false;
        state.candidateItems = Array.isArray(payload.candidates) ? payload.candidates.map((x) => String(x || "").trim()).filter(Boolean) : [];
        if (patchMainDynamic()) return;
        break;
      case "multi-candidates-error":
        if (state.candidateReqId && Number(payload.reqId || 0) !== state.candidateReqId) {
          break;
        }
        state.candidatePending = false;
        showToast("error", payload.message || "候选生成失败");
        if (patchMainDynamic()) return;
        break;
      case "history-updated":
        state.ui.history = payload.history || [];
        if (state.historyOpen) {
          void loadHistory(true);
        }
        break;
      case "ai-test-result":
        state.testingAi = false;
        state.aiTestState = payload.ok ? "success" : "error";
        state.aiAvailable = Boolean(payload.ok);
        state.aiAvailableChecked = true;
        state.aiAvailabilityMessage = String(payload.message || "");
        state.aiAvailabilityCheckedAt = Number(payload.checkedAt || Math.floor(Date.now() / 1000));
        showToast(payload.ok ? "success" : "error", payload.message || "");
        break;
      case "ai-availability":
        state.aiAvailable = Boolean(payload.available);
        state.aiAvailableChecked = Boolean(payload.checked);
        state.aiAvailabilityMessage = String(payload.message || "");
        state.aiAvailabilityCheckedAt = Number(payload.checkedAt || state.aiAvailabilityCheckedAt || 0);
        if (state.view === "main" && patchMainDynamic()) return;
        if (state.view === "bubble" && patchBubbleDynamic()) return;
        break;
      case "offline-models-updated":
        if (payload.config) state.config = payload.config;
        if (payload.settings) state.settings = payload.settings;
        state.settingsDraft = clone(state.config || {});
        state.notice = { type: "success", text: "Argos 模型已更新" };
        break;
      case "offline-model-import-error":
        state.notice = { type: "error", text: payload.message || "模型导入失败" };
        break;
      case "zoom-open":
        state.zoomPayload = payload;
        state.zoomOpen = true;
        if (state.zoomPayload && !state.zoomPayload.origin) {
          state.zoomPayload.origin = "main";
        }
        if (state.zoomPayload.origin !== "bubble" && typeof state.resultText === "string" && state.pending) {
          state.zoomPayload.resultText = state.resultText;
        }
        break;
      case "bubble-updated":
        {
          const prevSource = String(state.bubble?.source_text || "");
          const nextBubble = payload.bubble || state.bubble;
          state.bubble = nextBubble;
          if (nextBubble) {
            const hasCandidateData = Boolean(nextBubble.candidate_pending || (nextBubble.candidate_items || []).length);
            if (hasCandidateData) {
              state.bubbleCandidatePaneOpen = true;
            } else if ((nextBubble.mode || "argos") !== "ai") {
              state.bubbleCandidatePaneOpen = false;
            } else if (String(nextBubble.source_text || "") !== prevSource) {
              state.bubbleCandidatePaneOpen = false;
            }
          }
        }
        if (payload.themeMode) setTheme(payload.themeMode);
        if (state.zoomOpen && state.zoomPayload && state.zoomPayload.origin === "bubble" && payload.bubble) {
          state.zoomPayload.sourceText = payload.bubble.source_text || state.zoomPayload.sourceText || "";
          state.zoomPayload.resultText = payload.bubble.result_text || "";
          state.zoomPayload.action = payload.bubble.action || state.zoomPayload.action;
          state.zoomPayload.mode = payload.bubble.mode || state.zoomPayload.mode;
          const zoomSource = document.getElementById("zoomSource");
          const zoomResult = document.getElementById("zoomResult");
          if (zoomSource) zoomSource.textContent = state.zoomPayload.sourceText || "";
          if (zoomResult) zoomResult.textContent = state.zoomPayload.resultText || "";
          applyAutoScroll("zoom-shell");
        }
        if (state.bubble?.pending && !(state.bubble.result_text || "")) {
          scrollFollowState["bubble-result"] = true;
        }
        if (state.view === "bubble") {
          if (patchBubbleDynamic()) return;
        } else {
          shouldRerender = false;
        }
        break;
      case "bubble-translation-chunk":
        if (state.zoomOpen && state.zoomPayload && state.zoomPayload.origin === "bubble") {
          state.zoomPayload.sourceText = payload.sourceText || state.zoomPayload.sourceText || "";
          state.zoomPayload.resultText = payload.resultText || state.zoomPayload.resultText || "";
          const zoomSource = document.getElementById("zoomSource");
          const zoomResult = document.getElementById("zoomResult");
          if (zoomSource) zoomSource.textContent = state.zoomPayload.sourceText || "";
          if (zoomResult) zoomResult.textContent = state.zoomPayload.resultText || "";
          applyAutoScroll("zoom-shell");
        }
        shouldRerender = false;
        break;
      case "bubble-translation-done":
        if (state.zoomOpen && state.zoomPayload && state.zoomPayload.origin === "bubble") {
          state.zoomPayload.sourceText = payload.sourceText || state.zoomPayload.sourceText || "";
          state.zoomPayload.resultText = payload.resultText || state.zoomPayload.resultText || "";
          const zoomSource = document.getElementById("zoomSource");
          const zoomResult = document.getElementById("zoomResult");
          if (zoomSource) zoomSource.textContent = state.zoomPayload.sourceText || "";
          if (zoomResult) zoomResult.textContent = state.zoomPayload.resultText || "";
          applyAutoScroll("zoom-shell");
        }
        shouldRerender = false;
        break;
      case "bubble-translation-error":
        if (state.zoomOpen && state.zoomPayload && state.zoomPayload.origin === "bubble") {
          const resultText = payload.resultText ? `${payload.resultText}\n\n${payload.message}` : (payload.message || "");
          state.zoomPayload.sourceText = payload.sourceText || state.zoomPayload.sourceText || "";
          state.zoomPayload.resultText = resultText;
          const zoomSource = document.getElementById("zoomSource");
          const zoomResult = document.getElementById("zoomResult");
          if (zoomSource) zoomSource.textContent = state.zoomPayload.sourceText || "";
          if (zoomResult) zoomResult.textContent = state.zoomPayload.resultText || "";
          applyAutoScroll("zoom-shell");
        }
        shouldRerender = false;
        break;
      case "theme-updated":
        if (payload.bubble) state.bubble = payload.bubble;
        if (payload.themeMode) setTheme(payload.themeMode);
        if (payload.mode) {
          if (state.config) state.config.translation_mode = payload.mode;
          state.ui.translation_mode = payload.mode;
        }
        if (state.view === "bubble" && patchBubbleDynamic()) return;
        break;
      case "screenshot-ocr-ready":
        state.sourceText = payload.sourceText || state.sourceText;
        if (patchMainDynamic()) return;
        break;
      case "screenshot-ocr-error":
        state.notice = { type: "error", text: payload.message || "截图识别失败" };
        break;
      case "tray-open-panel":
        {
          const panel = String(payload.panel || "").toLowerCase();
          if (panel === "history") {
            void openHistoryPanel();
          } else if (panel === "settings") {
            void openSettingsPanel();
          }
          shouldRerender = false;
        }
        break;
      case "tray-show-main":
        showMainFromTray();
        shouldRerender = false;
        break;
      default:
        break;
    }
    if (shouldRerender) {
      rerender();
    }
  }

  function handleKeydown(event) {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;

    if (target.classList.contains("history-search")) {
      if (event.key === "Enter") {
        event.preventDefault();
        keepHistorySearchFocus = true;
        scheduleHistorySearch(true);
      } else if (event.key === "Escape") {
        event.preventDefault();
        state.historyPanel.q = "";
        target.value = "";
        keepHistorySearchFocus = true;
        scheduleHistorySearch(true);
      }
      return;
    }

    const shortcutField = target.dataset.shortcutField;
    if (!shortcutField) return;

    event.preventDefault();
    event.stopPropagation();
    const shortcut = captureShortcut(event);
    if (shortcut === null) return;
    if (!state.settingsDraft) {
      state.settingsDraft = clone(state.config || {});
    }
    setValue(state.settingsDraft, shortcutField, shortcut);
    target.value = shortcut;
  }

  window.WordPack = {
    receive(event, payload) {
      handleBackendEvent(event, payload || {});
    },
  };

  document.addEventListener("click", (event) => {
    void handleClick(event);
  });

  document.addEventListener("input", handleInput);
  document.addEventListener("change", handleInput);
  document.addEventListener("keydown", handleKeydown);
  document.addEventListener("scroll", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    if (target.id === "historyList") {
      state.historyPanel.scrollTop = target.scrollTop;
      return;
    }
    const key = target.dataset.autoscroll;
    if (!key) return;
    scrollFollowState[key] = isNearScrollEnd(target);
  }, true);
  document.addEventListener("mousedown", () => {
    if (state.view === "main" || state.view === "bubble") {
      void apiCall("notify_window_interaction");
    }
  });

  async function bootstrap() {
    const payload = await apiCall("bootstrap");
    if (!payload) return;
    applyBootstrap(payload);
    rerender();
    if (state.view === "bubble") {
      const settingsPayload = await apiCall("load_settings");
      if (settingsPayload) {
        state.settings = settingsPayload;
        if (!state.config && settingsPayload.config) {
          state.config = settingsPayload.config;
        }
        rerender();
      }
    }
    void apiCall("mark_ready");
  }

  window.addEventListener("pywebviewready", () => {
    void bootstrap();
  });

  render();
  installDragHandles();
  installTooltipDelegation();

  if (window.pywebview) {
    void bootstrap();
  }
})();






