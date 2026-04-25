const root = document.documentElement;
const body = document.body;
const prefersReduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const copy = {
  zh: {
    metaTitle: "WordPack 词小包｜Windows 高频翻译助手",
    metaDescription: "WordPack 是一款为 Windows 高频使用场景打造的桌面翻译工具，支持划词、截图、AI、离线词典与语音播放。",
    brand: { subtitle: "词小包" },
    nav: { demo: "界面", workflow: "流程", features: "能力", download: "开始" },
    hero: {
      eyebrow: "Windows 高频翻译助手",
      title: "把翻译变成系统级的肌肉记忆。",
      lead: "WordPack 连接划词、截图 OCR、AI 翻译、离线词典和语音播放。无需打断当前工作流，在任何窗口里快速读懂外文内容。",
      fact1: { k: "触发", v: "划词 / 热键 / 托盘" },
      fact2: { k: "引擎", v: "AI + Argos 离线" },
      fact3: { k: "平台", v: "Windows 桌面" },
      float1: { k: "Selection", v: "选中文字，就地翻译" },
      float2: { v: "截图 OCR 翻译" },
      deviceTitle: "Windows 桌面工作流",
    },
    cta: { download: "下载最新版", demo: "查看功能演示", source: "查看源码" },
    demo: {
      eyebrow: "界面一览",
      title: "四个界面，各司其职。",
      main: { title: "主界面", body: "用于输入翻译、查看结果、复制内容和播放译文，是长文本处理的核心工作区。" },
      history: { title: "历史界面", body: "保存已翻译内容，方便回看、收藏和复用。" },
      settings: { title: "设置面板", body: "集中管理主题、语言、热键、翻译模式和 AI 配置。" },
      tray: { title: "托盘面板", body: "常驻任务栏，提供主窗口、截图翻译和退出等快捷操作。" },
    },
    features: {
      eyebrow: "核心能力",
      title: "高频翻译需要的能力，一次到位。",
      ai: { title: "可配置 AI 翻译", body: "在设置中填写 Base URL、API Key 和模型，即可对接你偏好的 AI 翻译服务。" },
      offline: { title: "离线词典兜底", body: "通过 Argos 模型包支持离线翻译，网络不可用时仍然可以继续阅读。" },
      settings: { title: "设置项直观可控", body: "主题、语言、翻译模式、热键和 AI 配置集中管理。" },
      tts: { title: "译文语音播放", body: "使用 Windows SAPI 离线朗读译文，帮助听读、复核和记忆。" },
      history: { title: "历史记录可复用", body: "把翻译结果沉淀为可查找、可收藏、可复制的资料片段。" },
      tray: { title: "托盘快捷控制", body: "常驻任务栏托盘，快速打开主窗口、截图翻译或调整使用状态。" },
    },
    workflow: {
      eyebrow: "工作流",
      title: "随手触发，立即翻译。",
      step1: "划词", step2: "截图", step3: "输入",
      cards: [
        { eyebrow: "Step 01", title: "在当前应用里选中文本", body: "保持阅读上下文不变，通过配置的方式触发划词翻译。" },
        { eyebrow: "Step 02", title: "框选屏幕上的不可复制文字", body: "默认 Ctrl+Alt+S 进入截图翻译，适合图片、视频字幕和扫描件。" },
        { eyebrow: "Step 03", title: "在主窗口处理长文本", body: "输入或粘贴文本后翻译，结果可复制、朗读，也能继续沉淀到历史记录。" },
      ],
    },
    download: {
      eyebrow: "开源 · Windows 优先",
      title: "现在开始使用 WordPack。",
      body: "源码运行只需安装依赖并启动 python app.py；也可以在 Releases 中获取打包版本。",
    },
    images: {
      mainTranslate: "WordPack 输入翻译演示",
      selection: "WordPack 划词翻译演示",
      screenshot: "WordPack 截图翻译演示",
      settings: "WordPack 设置界面",
      history: "WordPack 历史记录界面",
      mainWindow: "WordPack 主界面",
      tray: "WordPack 托盘面板",
    },
  },
  en: {
    metaTitle: "WordPack｜A Windows translator for high-frequency work",
    metaDescription: "WordPack is a Windows desktop translator for selection translation, screenshot OCR translation, AI translation, offline dictionary translation, and text-to-speech.",
    brand: { subtitle: "Desktop translator" },
    nav: { demo: "Interface", workflow: "Workflow", features: "Capabilities", download: "Get Started" },
    hero: {
      eyebrow: "Windows translator for high-frequency work",
      title: "Make translation feel like system muscle memory.",
      lead: "WordPack brings selection translation, screenshot OCR, AI translation, offline dictionaries, and text-to-speech into one fast desktop workflow.",
      fact1: { k: "Trigger", v: "Selection / Hotkey / Tray" },
      fact2: { k: "Engines", v: "AI + Argos offline" },
      fact3: { k: "Platform", v: "Windows desktop" },
      float1: { k: "Selection", v: "Highlight text. Translate in place." },
      float2: { v: "Screenshot OCR translation" },
      deviceTitle: "Windows desktop workflow",
    },
    cta: { download: "Download latest", demo: "View demos", source: "View source" },
    demo: {
      eyebrow: "Interface tour",
      title: "Four interfaces, clear responsibilities.",
      main: { title: "Main window", body: "Translate typed or pasted text, review results, copy content, and play translated speech." },
      history: { title: "History", body: "Keep translated content available for review, favorites, and reuse." },
      settings: { title: "Settings panel", body: "Manage theme, language, hotkeys, translation mode, and AI configuration in one place." },
      tray: { title: "Tray panel", body: "Stay in the taskbar with quick access to the main window, screenshot translation, and exit actions." },
    },
    features: {
      eyebrow: "Core capabilities",
      title: "Capabilities built for frequent translation.",
      ai: { title: "Configurable AI translation", body: "Set Base URL, API Key, and model in Settings to connect your preferred AI translation service." },
      offline: { title: "Offline dictionary fallback", body: "Argos model packages keep translation available when the network is unavailable." },
      settings: { title: "Clear, controllable settings", body: "Theme, language, translation mode, hotkeys, and AI configuration live in one place." },
      tts: { title: "Translated text playback", body: "Windows SAPI reads translated text offline for listening, review, and memorization." },
      history: { title: "Reusable history", body: "Keep translation results searchable, favoriteable, and ready to copy later." },
      tray: { title: "Quick tray controls", body: "Keep WordPack in the tray and quickly open the main window or start screenshot translation." },
    },
    workflow: {
      eyebrow: "Workflow",
      title: "Trigger anytime, translate instantly.",
      step1: "Select", step2: "Capture", step3: "Input",
      cards: [
        { eyebrow: "Step 01", title: "Select text in your current app", body: "Stay in context and trigger selection translation using your configured mode." },
        { eyebrow: "Step 02", title: "Capture text that cannot be selected", body: "Use Ctrl+Alt+S by default for images, subtitles, screenshots, and scanned content." },
        { eyebrow: "Step 03", title: "Process longer text in the main window", body: "Type or paste text, then copy, listen to, and review the translation result." },
      ],
    },
    download: {
      eyebrow: "Open source · Windows first",
      title: "Start using WordPack today.",
      body: "Run from source with python app.py after installing dependencies, or download a packaged build from Releases.",
    },
    images: {
      mainTranslate: "WordPack input translation demo",
      selection: "WordPack selection translation demo",
      screenshot: "WordPack screenshot translation demo",
      settings: "WordPack settings screen",
      history: "WordPack history screen",
      mainWindow: "WordPack main window",
      tray: "WordPack tray panel",
    },
  },
};

const workflowMedia = {
  zh: [
    { src: "./assets/demo-selection-cn.gif", altKey: "images.selection" },
    { src: "./assets/demo-screenshot-translate--cn.gif", altKey: "images.screenshot" },
    { src: "./assets/demo-main-translate-cn.gif", altKey: "images.mainTranslate" },
  ],
  en: [
    { src: "./assets/demo-selection-en.gif", altKey: "images.selection" },
    { src: "./assets/demo-screenshot-translate-en.gif", altKey: "images.screenshot" },
    { src: "./assets/demo-main-translate-en.gif", altKey: "images.mainTranslate" },
  ],
};

const imagePairs = {
  zh: {
    "demo-selection-en.gif": "demo-selection-cn.gif",
    "demo-screenshot-translate-en.gif": "demo-screenshot-translate--cn.gif",
    "demo-main-translate-en.gif": "demo-main-translate-cn.gif",
    "demo-settings-en.png": "demo-settings-cn.png",
    "demo-history-en.png": "demo-history-cn.png",
    "demo-main-en.png": "demo-main-cn.png",
    "demo-tray-en.png": "demo-tray-cn.png",
  },
  en: {
    "demo-selection-cn.gif": "demo-selection-en.gif",
    "demo-screenshot-translate--cn.gif": "demo-screenshot-translate-en.gif",
    "demo-main-translate-cn.gif": "demo-main-translate-en.gif",
    "demo-settings-cn.png": "demo-settings-en.png",
    "demo-history-cn.png": "demo-history-en.png",
    "demo-main-cn.png": "demo-main-en.png",
    "demo-tray-cn.png": "demo-tray-en.png",
  },
};

const getValue = (object, path) => path.split(".").reduce((acc, key) => acc?.[key], object);
let currentLang = localStorage.getItem("wordpack-site-lang") || (navigator.language?.toLowerCase().startsWith("zh") ? "zh" : "en");
let currentWorkflowStep = 0;

function applyLanguage(lang) {
  currentLang = lang === "en" ? "en" : "zh";
  const dictionary = copy[currentLang];
  root.lang = currentLang === "zh" ? "zh-CN" : "en";
  document.title = dictionary.metaTitle;
  document.querySelector('meta[name="description"]')?.setAttribute("content", dictionary.metaDescription);

  document.querySelectorAll("[data-i18n]").forEach((node) => {
    const value = getValue(dictionary, node.dataset.i18n);
    if (typeof value === "string") node.textContent = value;
  });

  document.querySelectorAll("[data-i18n-attr]").forEach((node) => {
    node.dataset.i18nAttr.split(",").forEach((binding) => {
      const [attribute, key] = binding.split(":");
      const value = getValue(dictionary, key);
      if (attribute && typeof value === "string") node.setAttribute(attribute, value);
    });
  });

  document.querySelectorAll("img[src*='./assets/']").forEach((image) => {
    const file = image.getAttribute("src")?.split("/").pop();
    const replacement = file ? imagePairs[currentLang][file] : null;
    if (replacement) image.setAttribute("src", `./assets/${replacement}`);
  });

  document.querySelectorAll(".lang-button").forEach((button) => button.classList.toggle("active", button.dataset.lang === currentLang));
  localStorage.setItem("wordpack-site-lang", currentLang);
  renderWorkflow(currentWorkflowStep);
}

function renderWorkflow(index) {
  currentWorkflowStep = index;
  const item = copy[currentLang].workflow.cards[index];
  const media = workflowMedia[currentLang][index];
  const workflowCopy = document.querySelector(".workflow-copy");
  const workflowImage = document.querySelector(".workflow-visual img");
  document.querySelectorAll(".step").forEach((step) => step.classList.toggle("active", Number(step.dataset.step) === index));
  if (workflowImage && media) {
    workflowImage.src = media.src;
    workflowImage.alt = getValue(copy[currentLang], media.altKey) || "WordPack workflow demo";
  }
  if (workflowCopy && item) {
    workflowCopy.classList.remove("is-changing");
    void workflowCopy.offsetWidth;
    workflowCopy.innerHTML = `<span class="eyebrow">${item.eyebrow}</span><h3>${item.title}</h3><p>${item.body}</p>`;
    workflowCopy.classList.add("is-changing");
  }
}

const revealObserver = new IntersectionObserver((entries) => {
  entries.forEach((entry) => {
    if (entry.isIntersecting) {
      entry.target.classList.add("is-visible");
      revealObserver.unobserve(entry.target);
    }
  });
}, { threshold: 0.16 });

document.querySelectorAll("[data-reveal]").forEach((element) => revealObserver.observe(element));

document.querySelectorAll(".lang-button").forEach((button) => {
  button.addEventListener("click", () => applyLanguage(button.dataset.lang));
});

const themeButton = document.querySelector(".theme-toggle");
const savedTheme = localStorage.getItem("wordpack-site-theme");
if (savedTheme === "light") {
  body.classList.add("theme-light");
  themeButton?.setAttribute("aria-pressed", "true");
}

themeButton?.addEventListener("click", () => {
  const isLight = body.classList.toggle("theme-light");
  themeButton.setAttribute("aria-pressed", String(isLight));
  localStorage.setItem("wordpack-site-theme", isLight ? "light" : "dark");
});

if (!prefersReduced) {
  window.addEventListener("pointermove", (event) => {
    root.style.setProperty("--x", `${event.clientX}px`);
    root.style.setProperty("--y", `${event.clientY}px`);
  });

  document.querySelectorAll(".tilt-card").forEach((card) => {
    card.addEventListener("pointermove", (event) => {
      const rect = card.getBoundingClientRect();
      const x = (event.clientX - rect.left) / rect.width - 0.5;
      const y = (event.clientY - rect.top) / rect.height - 0.5;
      card.style.transform = `rotateX(${y * -5}deg) rotateY(${x * 6}deg) translateZ(0)`;
    });
    card.addEventListener("pointerleave", () => {
      card.style.transform = "rotateX(0deg) rotateY(0deg) translateZ(0)";
    });
  });

  document.querySelectorAll(".magnetic").forEach((button) => {
    button.addEventListener("pointermove", (event) => {
      const rect = button.getBoundingClientRect();
      const x = event.clientX - rect.left - rect.width / 2;
      const y = event.clientY - rect.top - rect.height / 2;
      button.style.transform = `translate(${x * 0.06}px, ${y * 0.08}px) translateY(-2px)`;
    });
    button.addEventListener("pointerleave", () => {
      button.style.transform = "";
    });
  });
}


const progressBar = document.querySelector(".scroll-progress span");
const updateScrollProgress = () => {
  if (!progressBar) return;
  const scrollable = document.documentElement.scrollHeight - window.innerHeight;
  const ratio = scrollable > 0 ? window.scrollY / scrollable : 0;
  progressBar.style.transform = `scaleX(${Math.min(1, Math.max(0, ratio))})`;
};
window.addEventListener("scroll", updateScrollProgress, { passive: true });
window.addEventListener("resize", updateScrollProgress);
updateScrollProgress();

document.querySelectorAll('a[href^="#"]').forEach((anchor) => {
  anchor.addEventListener("click", (event) => {
    const id = anchor.getAttribute("href");
    if (!id || id === "#") return;
    const target = document.querySelector(id);
    if (!target) return;
    event.preventDefault();
    target.scrollIntoView({ behavior: prefersReduced ? "auto" : "smooth", block: "start" });
    history.pushState(null, "", id);
  });
});

document.querySelectorAll(".step").forEach((button) => {
  button.addEventListener("click", () => renderWorkflow(Number(button.dataset.step || 0)));
});

const initInterfaceCarousel = () => {
  const carousel = document.querySelector(".interface-carousel");
  if (!carousel) return;

  const track = carousel.querySelector(".carousel-track");
  const slides = Array.from(carousel.querySelectorAll(".carousel-slide"));
  const dots = Array.from(carousel.querySelectorAll(".carousel-dots .dot"));
  const prev = carousel.querySelector(".carousel-nav.prev");
  const next = carousel.querySelector(".carousel-nav.next");
  const viewport = carousel.querySelector(".carousel-viewport");
  if (!track || slides.length === 0) return;

  let current = 0;
  let dragStartX = null;
  let dragDeltaX = 0;

  const setSlide = (index, instant = false) => {
    current = (index + slides.length) % slides.length;
    track.style.transitionDuration = instant ? "0ms" : "";
    track.style.transform = `translateX(-${current * 100}%)`;
    dots.forEach((dot, idx) => {
      const active = idx === current;
      dot.classList.toggle("active", active);
      dot.setAttribute("aria-selected", active ? "true" : "false");
    });
  };

  prev?.addEventListener("click", () => setSlide(current - 1));
  next?.addEventListener("click", () => setSlide(current + 1));
  dots.forEach((dot, index) => {
    dot.addEventListener("click", () => setSlide(index));
  });

  carousel.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      setSlide(current - 1);
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      setSlide(current + 1);
    }
  });

  viewport?.addEventListener("pointerdown", (event) => {
    dragStartX = event.clientX;
    dragDeltaX = 0;
  });
  viewport?.addEventListener("pointermove", (event) => {
    if (dragStartX == null) return;
    dragDeltaX = event.clientX - dragStartX;
  });

  const finalizeSwipe = () => {
    if (dragStartX == null) return;
    if (Math.abs(dragDeltaX) > 54) {
      setSlide(current + (dragDeltaX > 0 ? -1 : 1));
    }
    dragStartX = null;
    dragDeltaX = 0;
  };

  viewport?.addEventListener("pointerup", finalizeSwipe);
  viewport?.addEventListener("pointercancel", finalizeSwipe);
  viewport?.addEventListener("pointerleave", finalizeSwipe);

  setSlide(0, true);
};

initInterfaceCarousel();
applyLanguage(currentLang);











