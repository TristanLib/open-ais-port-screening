const DATA_DIR = "./data/";
const FALLBACK_CENTER = [37.84, -122.36];
const FALLBACK_ZOOM = 10;

const LAYER_COLORS = {
  traffic_density: "#2f80ed",
  traffic_patterns: "#219653",
  anomaly_points: "#d64545",
  risk_hotspots: "#f2994a",
  encounter_points: "#7b61ff",
  fused_risk_hotspots: "#c93675",
  case_tracks: "#008c95",
};

const DEFAULT_VISIBLE = new Set(["traffic_density", "fused_risk_hotspots", "case_tracks"]);
const LANGUAGE_STORAGE_KEY = "openAisExplorerLanguage";
const DATASET_STORAGE_KEY = "openAisExplorerDataset";

const COPY = {
  en: {
    htmlLang: "en",
    documentTitle: "Open AIS Port Screening Explorer",
    eyebrow: "Open AIS · two-waterway evidence",
    appTitle: "Open AIS Port Screening Explorer",
    loadingData: "Loading data",
    demoNotice:
      "Historical AIS first-pass screening evidence demo. Not real-time, not for navigation, not for enforcement, and not for operational decision-making.",
    languageAria: "Language",
    datasetAria: "Study area",
    metricLabels: {
      clean: "clean AIS points",
      episodes: "audit episodes",
      hotspots: "review-priority cells",
    },
    sections: {
      evidenceChain: "Evidence Chain",
      auditEvidence: "Audit Evidence",
      evidenceCards: "CPA/TCPA Encounter Evidence Cards",
      layers: "Layers",
      reproducibility: "Data & Reproducibility",
      cases: "Cases",
    },
    chainSteps: [
      ["01", "Exposure baseline", "Traffic density shows where vessels are numerous, not where risk is proven."],
      ["02", "Regular patterns", "Learned moving-traffic cells define local route expectations."],
      ["03", "Candidate evidence", "Anomaly and future encounter records identify what deserves review."],
      ["04", "Review-priority cells", "Evidence layers are aggregated into auditable spatial review units."],
      ["05", "Encounter evidence cards", "De-identified relative-time cards expose the CPA/TCPA prediction, strict-future coverage, and geometric outcome for audit."],
      ["06", "Case audit", "Representative tracks explain why candidates were selected."],
    ],
    layerLabels: {
      traffic_density: "Traffic density",
      traffic_patterns: "Regular traffic pattern cells",
      anomaly_points: "Anomaly candidate points",
      risk_hotspots: "Anomaly-only evidence hotspot cells",
      encounter_points: "CPA/TCPA future encounter candidate records",
      fused_risk_hotspots: "Fused review-priority cells",
      case_tracks: "Representative de-identified case tracks",
    },
    layerRoles: {
      traffic_density: "Exposure baseline; density is not a safety conclusion.",
      traffic_patterns: "Local regular-route evidence for deviation checks.",
      anomaly_points: "Candidate behavior evidence with interpretable reasons.",
      risk_hotspots: "Behavior-only comparison layer for ablation.",
      encounter_points: "Future CPA/TCPA candidate records, not near-miss labels.",
      fused_risk_hotspots: "Review-priority cells combining behavioral and encounter evidence.",
      case_tracks: "De-identified tracks for human audit and explanation.",
    },
    layerFallback: "Auditable first-pass screening layer.",
    popupLabels: {
      screening_score: "screening score",
      point_count: "AIS points",
      moving_count: "moving points",
      anomaly_count: "anomaly candidates",
      corroborated_anomaly_count: "corroborated candidates",
      low_support_only_count: "low-support-only candidates",
      encounter_count: "encounter records",
      pair_opportunity_count: "evaluated pair opportunities",
      dominant_reason: "dominant reason",
      reasons: "candidate reasons",
      hotspot_type: "hotspot type",
      dominant_evidence: "dominant evidence",
      stability_class: "stability class",
      review_focus: "review focus",
      case_reason: "case type",
      date: "date",
      dcpa_nm: "DCPA (nm)",
      tcpa_min: "TCPA (min)",
      current_distance_nm: "current distance (nm)",
      state_skew_s: "state timestamp skew (s)",
      track_point_count: "track points",
      coordinate_count: "display points",
      mean_screening_score: "mean screening score",
      max_screening_score: "max screening score",
      density_topk_days: "density top-k days",
      fused_topk_days: "fused top-k days",
    },
    validationItems: [
      ["Clean AIS points", "dataset.clean_ais_points"],
      ["Corroborated candidates", "anomaly_detection.corroborated_candidate_points"],
      ["Low-support only", "anomaly_detection.low_support_only_points"],
      ["Strict-future support", "encounter_backtest.support_rate_observable_percent"],
      ["Observable follow-up", "encounter_backtest.observable_rate_percent"],
      ["Encounter records", "encounter_risk.encounters"],
      ["Audit encounter episodes", "encounter_risk.deduplicated_encounter_episodes"],
      ["Review-priority cells", "hotspots.fused_hotspots"],
    ],
    evidenceCardsEmpty: "No encounter evidence cards loaded for this study area.",
    encounterCardLabels: {
      prediction: "Prediction",
      observed: "Continuous future minimum",
      dcpa: "predicted DCPA",
      tcpa: "predicted TCPA",
      actualDistance: "observed minimum",
      timeError: "closest-time error",
      sourceSkew: "source-state skew",
      coverage: "common coverage",
      samples: "common samples",
      maxGap: "maximum uncovered gap",
      sensitivity: "10 / 30 / 60 s sensitivity",
      futureGeometry: "De-identified future geometry",
      unavailableGeometry: "Relative future geometry is unavailable for this card.",
      vesselA: "vessel A",
      vesselB: "vessel B",
      predictedMarker: "predicted closest positions",
      observedMarker: "observed minimum-time positions",
      boundary: "Candidate-screening geometric support only; not prediction accuracy or an event label.",
      seconds: "s",
      minutes: "min",
      nauticalMiles: "nm",
      supported05: "geometrically supported within 0.5 nm",
      supported10: "geometrically supported within 1.0 nm only",
      unsupported: "observable without 1.0 nm geometric support",
      insufficient: "insufficient common future coverage",
    },
    cardCounts: {
      episodes: "eps",
      supported: "future-supported",
      anomalies: "corroborated",
    },
    reproRows: [
      ["Source", "source"],
      ["Role", "workflow_role"],
      ["Study area", "study_area"],
      ["Window", "date_range"],
      ["Public data", "Sanitized derived GeoJSON/JSON only"],
      ["Rebuild guide", "docs/REPRODUCIBILITY.md"],
      ["Smoke check", "python3 src/sample_pipeline_smoke.py --clean-output"],
      ["Boundary", "Candidate screening evidence, not event labels"],
    ],
    caseMeta: "anomalies",
    status: {
      preparing: "Preparing map",
      loaded: "Loaded de-identified first-pass screening evidence layers",
      switching: "Loading study-area evidence",
      languageChanged: "Language switched to English",
      card: (card, episodes, supported) =>
        `${card.card_id}: ${translateText(card.hotspot_type)}; ${episodes} encounter episodes, ${supported} strict-future supported; ${translateText(
          card.review_focus,
        )}`,
      case: (props) =>
        `${props.case_id}: ${translateText(props.case_reason)}; auditable case track with ${formatNumber(
          props.track_point_count,
        )} displayed points`,
    },
    terms: {},
  },
  zh: {
    htmlLang: "zh-CN",
    documentTitle: "开放 AIS 港口筛查探索器",
    eyebrow: "开放 AIS · 双水域证据",
    appTitle: "开放 AIS 港口筛查探索器",
    loadingData: "正在加载数据",
    demoNotice: "历史 AIS 一次筛查证据演示。非实时系统，不用于导航、避碰、执法或运行决策。",
    languageAria: "语言切换",
    datasetAria: "研究水域",
    metricLabels: {
      clean: "清洗后 AIS 点",
      episodes: "会遇审计 episodes",
      hotspots: "复核优先网格",
    },
    sections: {
      evidenceChain: "证据链",
      auditEvidence: "审计证据",
      evidenceCards: "CPA/TCPA 会遇证据卡片",
      layers: "图层",
      reproducibility: "数据与复现",
      cases: "案例",
    },
    chainSteps: [
      ["01", "交通暴露基线", "交通密度说明船舶活动多寡，不等同于安全结论。"],
      ["02", "常规交通模式", "学习移动交通网格，用作本地航路期望。"],
      ["03", "候选证据", "异常候选与未来会遇候选指出值得复核的对象。"],
      ["04", "复核优先网格", "多层证据聚合成可审计的空间复核单元。"],
      ["05", "会遇证据卡片", "以去标识化相对时间卡片展示 CPA/TCPA 预测、严格未来覆盖和几何结果，供人工复核。"],
      ["06", "案例审查", "代表性轨迹解释候选对象为何被选中。"],
    ],
    layerLabels: {
      traffic_density: "AIS 交通密度",
      traffic_patterns: "经验常规交通模式网格",
      anomaly_points: "异常候选点",
      risk_hotspots: "仅异常证据热区网格",
      encounter_points: "CPA/TCPA 未来会遇候选记录",
      fused_risk_hotspots: "融合复核优先网格",
      case_tracks: "去标识化代表案例轨迹",
    },
    layerRoles: {
      traffic_density: "交通暴露基线；密度本身不是安全结论。",
      traffic_patterns: "本地常规航路证据，用于偏离检查。",
      anomaly_points: "带可解释原因的行为候选证据。",
      risk_hotspots: "用于消融对照的仅行为证据图层。",
      encounter_points: "未来 CPA/TCPA 候选记录，不是 near-miss 标签。",
      fused_risk_hotspots: "融合行为与会遇证据的复核优先网格。",
      case_tracks: "用于人工审计和解释的去标识化轨迹。",
    },
    layerFallback: "可审计的一次筛查图层。",
    popupLabels: {
      screening_score: "筛查分数",
      point_count: "AIS 点数",
      moving_count: "移动点数",
      anomaly_count: "异常候选数",
      corroborated_anomaly_count: "多证据候选数",
      low_support_only_count: "仅低航路支持候选数",
      encounter_count: "会遇记录数",
      pair_opportunity_count: "已评估船对机会数",
      dominant_reason: "主导原因",
      reasons: "候选原因",
      hotspot_type: "热区类型",
      dominant_evidence: "主导证据",
      stability_class: "稳定性类别",
      review_focus: "复核重点",
      case_reason: "案例类型",
      date: "日期",
      dcpa_nm: "DCPA（海里）",
      tcpa_min: "TCPA（分钟）",
      current_distance_nm: "当前距离（海里）",
      state_skew_s: "状态时间差（秒）",
      track_point_count: "轨迹点数",
      coordinate_count: "显示点数",
      mean_screening_score: "平均筛查分数",
      max_screening_score: "最高筛查分数",
      density_topk_days: "密度 top-k 天数",
      fused_topk_days: "融合 top-k 天数",
    },
    validationItems: [
      ["清洗后 AIS 点", "dataset.clean_ais_points"],
      ["多证据候选点", "anomaly_detection.corroborated_candidate_points"],
      ["仅低航路支持", "anomaly_detection.low_support_only_points"],
      ["严格未来支持率", "encounter_backtest.support_rate_observable_percent"],
      ["未来可观测率", "encounter_backtest.observable_rate_percent"],
      ["会遇候选 records", "encounter_risk.encounters"],
      ["会遇审计 episodes", "encounter_risk.deduplicated_encounter_episodes"],
      ["复核优先网格", "hotspots.fused_hotspots"],
    ],
    evidenceCardsEmpty: "当前研究水域未加载会遇证据卡片。",
    encounterCardLabels: {
      prediction: "预测",
      observed: "连续未来最小距离",
      dcpa: "预测 DCPA",
      tcpa: "预测 TCPA",
      actualDistance: "实际最小距离",
      timeError: "最近时刻误差",
      sourceSkew: "源状态时间差",
      coverage: "共同覆盖",
      samples: "共同样本",
      maxGap: "最大未覆盖缺口",
      sensitivity: "10 / 30 / 60 秒敏感性",
      futureGeometry: "去标识化未来几何",
      unavailableGeometry: "此卡片暂无可用的相对未来轨迹。",
      vesselA: "船舶 A",
      vesselB: "船舶 B",
      predictedMarker: "预测最近位置",
      observedMarker: "实际最小时刻位置",
      boundary: "仅表示候选筛查的几何支持，不是预测准确率或事件标签。",
      seconds: "秒",
      minutes: "分钟",
      nauticalMiles: "海里",
      supported05: "0.5 海里内几何支持",
      supported10: "仅 1.0 海里内几何支持",
      unsupported: "可观测但未获 1.0 海里内几何支持",
      insufficient: "共同未来覆盖不足",
    },
    cardCounts: {
      episodes: "episodes",
      supported: "严格未来支持",
      anomalies: "多证据候选",
    },
    reproRows: [
      ["数据源", "source"],
      ["验证角色", "workflow_role"],
      ["研究水域", "study_area"],
      ["时间窗口", "date_range"],
      ["公开数据", "仅使用脱敏派生 GeoJSON/JSON"],
      ["复现指南", "docs/REPRODUCIBILITY.md"],
      ["冒烟检查", "python3 src/sample_pipeline_smoke.py --clean-output"],
      ["边界", "候选筛查证据，不是事件标签"],
    ],
    caseMeta: "个异常候选",
    status: {
      preparing: "正在准备地图",
      loaded: "已加载去标识化一次筛查证据图层",
      switching: "正在加载研究水域证据",
      languageChanged: "已切换为中文",
      card: (card, episodes, supported) =>
        `${card.card_id}：${translateText(card.hotspot_type)}；${episodes} 个会遇 episodes，${supported} 个严格未来支持；${translateText(
          card.review_focus,
        )}`,
      case: (props) =>
        `${props.case_id}：${translateText(props.case_reason)}；可审计案例轨迹，显示 ${formatNumber(props.track_point_count)} 个点`,
    },
    terms: {
      "San Francisco Bay and Port of Oakland Approaches": "旧金山湾与奥克兰港进近水域",
      "San Francisco Bay and Port of Oakland approaches": "旧金山湾与奥克兰港进近水域",
      "Tokyo Bay": "东京湾",
      "Density-only": "仅密度",
      "Anomaly-only": "仅异常",
      "Encounter-only": "仅会遇",
      "Fused screening": "融合筛查",
      "Descriptive baseline": "描述性基线",
      "35 hotspot cells": "35 个热区网格",
      "56,221 records; 19,805 episodes": "56,221 条记录；19,805 个 episodes",
      "57 hotspot cells": "57 个热区网格",
      "High-density cells include berth/anchorage behavior and are not sufficient as safety evidence.":
        "高密度网格包含泊位、锚地等常规作业，不能单独作为安全证据。",
      "Finds behavior deviations but misses dense crossing/meeting evidence.": "能发现行为偏离，但会遗漏密集交叉/对遇证据。",
      "Captures close-quarters candidates but lacks behavioral context.": "能捕捉近距离会遇候选，但缺少行为背景。",
      "Combines anomaly evidence and future encounter-candidate evidence.": "融合异常证据与未来会遇候选证据。",
      "event-sensitive review hotspot": "事件敏感型复核热区",
      "stable-exposure review hotspot": "稳定交通暴露复核热区",
      "persistent candidate-evidence hotspot": "持续候选证据热区",
      "fused-evidence review hotspot": "融合证据复核热区",
      "encounter-dominated review hotspot": "会遇主导复核热区",
      "anomaly-dominated review hotspot": "异常主导复核热区",
      "encounter-dominated fused evidence": "会遇主导融合证据",
      "balanced anomaly-encounter evidence": "异常-会遇均衡证据",
      "anomaly-dominated fused evidence": "异常主导融合证据",
      "anomaly-only candidate evidence": "仅异常候选证据",
      "event-sensitive candidate hotspot": "事件敏感候选热区",
      "stable traffic-exposure hotspot": "稳定交通暴露热区",
      "high-exposure context cell": "高暴露背景网格",
      "intermittent evidence cell": "间歇证据网格",
      "Review close-quarters candidate episodes and local crossing or meeting context.":
        "复核近距离会遇候选 episodes 及本地交叉/对遇语境。",
      "Compare stable traffic exposure with candidate evidence; density alone is not a safety finding.":
        "对比稳定交通暴露与候选证据；密度本身不是安全结论。",
      "Review combined behavioral and encounter evidence before any operational interpretation.":
        "在作运行解释前，先复核行为与会遇的组合证据。",
      low_empirical_route_support: "经验航路支持度低",
      route_deviation: "注入式航路偏离",
      direction_mismatch: "方向不一致",
      high_turn: "高转向率",
      high_speed: "高速",
      high_accel: "高加速度",
      implied_speed: "隐含速度跳变",
      suspicious_stop: "异常低速/停驶",
      low_speed_stop: "低速/停驶",
    },
  },
};

function getInitialLanguage() {
  const saved = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
  if (saved === "en" || saved === "zh") return saved;
  return navigator.language && navigator.language.toLowerCase().startsWith("zh") ? "zh" : "en";
}

function getInitialDataset() {
  return window.localStorage.getItem(DATASET_STORAGE_KEY) || "";
}

const state = {
  map: null,
  basemap: null,
  catalog: null,
  activeDatasetId: getInitialDataset(),
  loadSequence: 0,
  manifest: null,
  summary: null,
  evidenceCards: null,
  layers: new Map(),
  layerData: new Map(),
  language: getInitialLanguage(),
};

function currentCopy() {
  return COPY[state.language] || COPY.en;
}

function localizedManifestValue(key) {
  if (!state.manifest) return "";
  if (state.language === "zh" && state.manifest[`${key}_zh`]) return state.manifest[`${key}_zh`];
  return state.manifest[key] || "";
}

function activeDatasetMeta() {
  return state.catalog?.datasets?.find((dataset) => dataset.id === state.activeDatasetId) || null;
}

function localizedDatasetLabel(dataset) {
  return dataset?.label?.[state.language] || dataset?.label?.en || dataset?.id || "";
}

function translateText(value) {
  if (value === undefined || value === null) return "";
  const text = String(value);
  return currentCopy().terms[text] || text;
}

function displayValue(value) {
  if (value === undefined || value === null || value === "") return "-";
  const number = Number(value);
  if (Number.isFinite(number) && String(value).trim() !== "") return formatNumber(number);
  return translateText(value);
}

function getSummaryValue(path) {
  if (!state.summary) return "-";
  if (path === "encounter_backtest.support_rate_observable_percent") {
    return formatPercent(Number(state.summary.encounter_backtest?.support_rate_observable) * 100);
  }
  if (path === "encounter_backtest.observable_rate_percent") {
    return formatPercent(Number(state.summary.encounter_backtest?.observable_rate) * 100);
  }
  const value = path.split(".").reduce((obj, key) => (obj ? obj[key] : undefined), state.summary);
  return path.endsWith("_percent") ? formatPercent(value) : formatNumber(value);
}

function formatNumber(value) {
  if (value === undefined || value === null || value === "") return "-";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  const locale = state.language === "zh" ? "zh-CN" : "en-US";
  return new Intl.NumberFormat(locale, { maximumFractionDigits: number < 10 ? 3 : 0 }).format(number);
}

function formatPercent(value) {
  if (value === undefined || value === null || value === "") return "-";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return `${number.toFixed(number < 10 ? 2 : 1)}%`;
}

function setStatus(text) {
  document.getElementById("statusBar").textContent = text;
}

function updateLanguageControls() {
  const copy = currentCopy();
  document.querySelector(".language-switch")?.setAttribute("aria-label", copy.languageAria);
  for (const button of document.querySelectorAll(".language-option")) {
    const active = button.dataset.lang === state.language;
    button.setAttribute("aria-pressed", String(active));
  }
}

function renderDatasetControls() {
  const container = document.getElementById("datasetSwitch");
  container.setAttribute("aria-label", currentCopy().datasetAria);
  container.innerHTML = "";
  for (const dataset of state.catalog?.datasets || []) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "dataset-option";
    button.dataset.datasetId = dataset.id;
    button.textContent = localizedDatasetLabel(dataset);
    button.setAttribute("aria-pressed", String(dataset.id === state.activeDatasetId));
    button.addEventListener("click", () => loadDataset(dataset.id));
    container.append(button);
  }
}

function updateDateRangeText() {
  const dateRange = state.manifest?.date_range || state.summary?.date_range;
  document.getElementById("dateRange").textContent = dateRange
    ? `${dateRange.start} ${state.language === "zh" ? "至" : "to"} ${dateRange.end}`
    : currentCopy().loadingData;
}

function renderStaticText() {
  const copy = currentCopy();
  document.documentElement.lang = copy.htmlLang;
  document.title = copy.documentTitle;
  const studyArea = localizedManifestValue("study_area");
  const role = localizedManifestValue("workflow_role");
  document.getElementById("eyebrowText").textContent = studyArea && role ? `${studyArea} · ${role}` : copy.eyebrow;
  document.getElementById("appTitle").textContent = copy.appTitle;
  document.getElementById("demoNotice").textContent = copy.demoNotice;
  document.getElementById("metricCleanLabel").textContent = copy.metricLabels.clean;
  document.getElementById("metricEpisodesLabel").textContent = copy.metricLabels.episodes;
  document.getElementById("metricHotspotsLabel").textContent = copy.metricLabels.hotspots;
  document.getElementById("evidenceChainTitle").textContent = copy.sections.evidenceChain;
  document.getElementById("auditEvidenceTitle").textContent = copy.sections.auditEvidence;
  document.getElementById("evidenceCardsTitle").textContent = copy.sections.evidenceCards;
  document.getElementById("layersTitle").textContent = copy.sections.layers;
  document.getElementById("reproTitle").textContent = copy.sections.reproducibility;
  document.getElementById("casesTitle").textContent = copy.sections.cases;
  updateDateRangeText();
  updateLanguageControls();
  renderDatasetControls();
}

function refreshLayerPopups() {
  for (const [layerId, mapLayer] of state.layers) {
    mapLayer.eachLayer((leafletLayer) => {
      if (leafletLayer.feature) leafletLayer.bindPopup(popupHtml(layerId, leafletLayer.feature));
    });
  }
}

function renderDynamicSections() {
  updateMetrics();
  renderEvidenceChain();
  renderValidation();
  renderEvidenceCards();
  renderLayerControls();
  renderReproducibility();
  renderCases();
  refreshLayerPopups();
}

function setLanguage(language) {
  if (language !== "en" && language !== "zh") return;
  state.language = language;
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
  renderStaticText();
  if (state.manifest) renderDynamicSections();
  setStatus(currentCopy().status.languageChanged);
}

function initLanguageControls() {
  document.querySelectorAll(".language-option").forEach((button) => {
    button.addEventListener("click", () => setLanguage(button.dataset.lang));
  });
  renderStaticText();
}

function layerStyle(layerId, feature) {
  const props = feature.properties || {};
  const color = LAYER_COLORS[layerId] || "#2f80ed";
  if (layerId === "traffic_density") {
    const value = Number(props.density_norm || 0);
    return {
      color,
      weight: 0,
      fillColor: color,
      fillOpacity: Math.max(0.05, Math.min(0.48, value * 0.5)),
    };
  }
  if (layerId === "traffic_patterns") {
    const high = Number(props.is_high_confidence_route_cell || 0) === 1;
    return {
      color: high ? "#156f40" : color,
      weight: high ? 1.2 : 0.6,
      fillColor: high ? "#27ae60" : color,
      fillOpacity: high ? 0.28 : 0.16,
    };
  }
  if (layerId === "risk_hotspots" || layerId === "fused_risk_hotspots") {
    const value = Number(props.screening_score ?? props.risk_score ?? 0);
    return {
      color,
      weight: 1.2,
      fillColor: color,
      fillOpacity: Math.max(0.24, Math.min(0.72, value * 0.72)),
    };
  }
  if (layerId === "case_tracks") {
    return {
      color,
      weight: 3,
      opacity: 0.86,
    };
  }
  return { color, weight: 1, fillOpacity: 0.2 };
}

function pointToLayer(layerId, feature, latlng) {
  const props = feature.properties || {};
  const color = LAYER_COLORS[layerId] || "#2f80ed";
  const score = Number(props.screening_score ?? 0.5);
  return L.circleMarker(latlng, {
    radius: Math.max(3, Math.min(8, 3 + score * 5)),
    color,
    fillColor: color,
    fillOpacity: 0.68,
    weight: 1,
  });
}

function popupHtml(layerId, feature) {
  const p = feature.properties || {};
  const layerMeta = state.manifest?.layers?.find((layer) => layer.id === layerId);
  const title = p.case_id || p.cell_id || p.date || currentCopy().layerLabels[layerId] || layerMeta?.label || layerId;
  const rows = [];
  const labels = currentCopy().popupLabels;
  const keys = [
    "screening_score",
    "point_count",
    "moving_count",
    "anomaly_count",
    "corroborated_anomaly_count",
    "low_support_only_count",
    "encounter_count",
    "pair_opportunity_count",
    "dominant_reason",
    "reasons",
    "hotspot_type",
    "dominant_evidence",
    "stability_class",
    "review_focus",
    "case_reason",
    "date",
    "dcpa_nm",
    "tcpa_min",
    "current_distance_nm",
    "state_skew_s",
    "track_point_count",
    "coordinate_count",
    "mean_screening_score",
    "max_screening_score",
    "density_topk_days",
    "fused_topk_days",
  ];
  for (const key of keys) {
    if (p[key] !== undefined && p[key] !== "") {
      rows.push(`<div class="popup-row"><span>${labels[key] || key}</span><strong>${displayValue(p[key])}</strong></div>`);
    }
  }
  return `<p class="popup-title">${title}</p>${rows.join("")}`;
}

async function loadJson(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`Failed to load ${path}`);
  return response.json();
}

function createGeoJsonLayer(layerId, data) {
  return L.geoJSON(data, {
    style: (feature) => layerStyle(layerId, feature),
    pointToLayer: (feature, latlng) => pointToLayer(layerId, feature, latlng),
    onEachFeature: (feature, layer) => {
      layer.bindPopup(popupHtml(layerId, feature));
    },
  });
}

function countFeatures(data) {
  return Array.isArray(data.features) ? data.features.length : 0;
}

function updateMetrics() {
  document.getElementById("metricClean").textContent = getSummaryValue("dataset.clean_ais_points");
  document.getElementById("metricEpisodes").textContent = getSummaryValue(
    "encounter_risk.deduplicated_encounter_episodes",
  );
  document.getElementById("metricHotspots").textContent = getSummaryValue("hotspots.fused_hotspots");
}

function renderEvidenceChain() {
  const container = document.getElementById("evidenceChain");
  container.innerHTML = "";
  const hasCards = Boolean(state.evidenceCards?.cards?.length);
  const hasCases = Boolean(state.layerData.get("case_tracks")?.features?.length);
  const steps = currentCopy().chainSteps.filter(([index]) => (index !== "05" || hasCards) && (index !== "06" || hasCases));
  for (const [index, title, note] of steps) {
    const item = document.createElement("div");
    item.className = "chain-item";
    const indexEl = document.createElement("span");
    indexEl.className = "chain-index";
    indexEl.textContent = index;
    const body = document.createElement("span");
    body.className = "chain-body";
    const titleEl = document.createElement("span");
    titleEl.className = "chain-title";
    titleEl.textContent = title;
    const noteEl = document.createElement("span");
    noteEl.className = "chain-note";
    noteEl.textContent = note;
    body.append(titleEl, noteEl);
    item.append(indexEl, body);
    container.append(item);
  }
}

function renderValidation() {
  const summary = state.summary;
  const metrics = document.getElementById("validationMetrics");
  const ablation = document.getElementById("ablationList");
  metrics.innerHTML = "";
  ablation.innerHTML = "";
  if (!summary) return;

  const items = currentCopy().validationItems.map(([label, path]) => [label, getSummaryValue(path)]);

  for (const [label, value] of items) {
    const item = document.createElement("div");
    item.className = "evidence-item";
    const valueEl = document.createElement("span");
    valueEl.className = "evidence-value";
    valueEl.textContent = value;
    const labelEl = document.createElement("span");
    labelEl.className = "evidence-label";
    labelEl.textContent = label;
    item.append(valueEl, labelEl);
    metrics.append(item);
  }

  for (const row of summary.ablation || []) {
    const item = document.createElement("div");
    item.className = "ablation-item";
    const name = document.createElement("span");
    name.className = "ablation-name";
    name.textContent = translateText(row.variant);
    const result = document.createElement("span");
    result.className = "ablation-result";
    result.textContent = translateText(row.result);
    const note = document.createElement("span");
    note.className = "ablation-note";
    note.textContent = translateText(row.interpretation);
    item.append(name, result, note);
    ablation.append(item);
  }
}

function hotspotFeatureByCell(cellId) {
  const data = state.layerData.get("fused_risk_hotspots");
  if (!data || !Array.isArray(data.features)) return null;
  return data.features.find((feature) => feature.properties?.cell_id === cellId) || null;
}

function encounterSupportLabel(status) {
  const labels = currentCopy().encounterCardLabels;
  const mapping = {
    geometrically_supported_within_0_5_nm: labels.supported05,
    geometrically_supported_within_1_0_nm_only: labels.supported10,
    observable_without_1_0_nm_geometric_support: labels.unsupported,
    insufficient_common_future_coverage: labels.insufficient,
  };
  return mapping[status] || translateText(status);
}

function encounterMetric(label, value) {
  const item = document.createElement("span");
  item.className = "encounter-metric";
  const valueEl = document.createElement("strong");
  valueEl.textContent = value;
  const labelEl = document.createElement("span");
  labelEl.textContent = label;
  item.append(valueEl, labelEl);
  return item;
}

function metricWithUnit(value, unit) {
  return value === undefined || value === null ? "-" : `${formatNumber(value)} ${unit}`;
}

function encounterGeometrySvg(card) {
  const labels = currentCopy().encounterCardLabels;
  const geometry = card.future_geometry || {};
  const segments = Array.isArray(geometry.common_segments) ? geometry.common_segments : [];
  if (geometry.status !== "available" || !segments.length) {
    const unavailable = document.createElement("p");
    unavailable.className = "geometry-unavailable";
    unavailable.textContent = labels.unavailableGeometry;
    return unavailable;
  }

  const t0Geometry = card.synchronized_t0?.relative_positions || {};
  const predictedPositions = Array.isArray(t0Geometry.predicted_closest_positions)
    ? t0Geometry.predicted_closest_positions
    : [];
  const observedPositions = Array.isArray(geometry.observed_minimum_positions)
    ? geometry.observed_minimum_positions
    : [];
  const coordinateItems = [];
  for (const segment of segments) {
    for (const vessel of segment.vessels || []) {
      for (const point of vessel.points || []) coordinateItems.push(point);
    }
  }
  coordinateItems.push(...predictedPositions, ...observedPositions);
  if (!coordinateItems.length) {
    const unavailable = document.createElement("p");
    unavailable.className = "geometry-unavailable";
    unavailable.textContent = labels.unavailableGeometry;
    return unavailable;
  }

  const width = 320;
  const height = 180;
  const padding = 20;
  const xs = coordinateItems.map((point) => Number(point.x_nm)).filter(Number.isFinite);
  const ys = coordinateItems.map((point) => Number(point.y_nm)).filter(Number.isFinite);
  let minX = Math.min(...xs);
  let maxX = Math.max(...xs);
  let minY = Math.min(...ys);
  let maxY = Math.max(...ys);
  if (maxX - minX < 0.05) {
    minX -= 0.025;
    maxX += 0.025;
  }
  if (maxY - minY < 0.05) {
    minY -= 0.025;
    maxY += 0.025;
  }
  const scale = Math.min((width - 2 * padding) / (maxX - minX), (height - 2 * padding) / (maxY - minY));
  const project = (point) => [
    padding + (Number(point.x_nm) - minX) * scale,
    height - padding - (Number(point.y_nm) - minY) * scale,
  ];
  const svgNamespace = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNamespace, "svg");
  svg.classList.add("encounter-geometry");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", labels.futureGeometry);

  const grid = document.createElementNS(svgNamespace, "rect");
  grid.setAttribute("x", "0");
  grid.setAttribute("y", "0");
  grid.setAttribute("width", String(width));
  grid.setAttribute("height", String(height));
  grid.setAttribute("class", "geometry-background");
  svg.append(grid);

  const colors = { A: "#2f80ed", B: "#d64545" };
  for (const segment of segments) {
    for (const vessel of segment.vessels || []) {
      const points = (vessel.points || []).map(project);
      if (!points.length) continue;
      const polyline = document.createElementNS(svgNamespace, "polyline");
      polyline.setAttribute("points", points.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" "));
      polyline.setAttribute("fill", "none");
      polyline.setAttribute("stroke", colors[vessel.label] || "#52606d");
      polyline.setAttribute("class", "geometry-track");
      svg.append(polyline);
    }
  }
  for (const point of predictedPositions) {
    const [x, y] = project(point);
    const marker = document.createElementNS(svgNamespace, "rect");
    marker.setAttribute("x", (x - 3.5).toFixed(2));
    marker.setAttribute("y", (y - 3.5).toFixed(2));
    marker.setAttribute("width", "7");
    marker.setAttribute("height", "7");
    marker.setAttribute("class", "predicted-marker");
    svg.append(marker);
  }
  for (const point of observedPositions) {
    const [x, y] = project(point);
    const marker = document.createElementNS(svgNamespace, "circle");
    marker.setAttribute("cx", x.toFixed(2));
    marker.setAttribute("cy", y.toFixed(2));
    marker.setAttribute("r", "4");
    marker.setAttribute("class", "observed-marker");
    svg.append(marker);
  }

  const wrap = document.createElement("div");
  wrap.className = "encounter-geometry-wrap";
  wrap.append(svg);
  const legend = document.createElement("div");
  legend.className = "geometry-legend";
  legend.textContent = `${labels.vesselA} · ${labels.vesselB} · □ ${labels.predictedMarker} · ○ ${labels.observedMarker}`;
  wrap.append(legend);
  return wrap;
}

function renderEncounterEvidenceCards(container, cards) {
  const labels = currentCopy().encounterCardLabels;
  for (const card of cards) {
    const details = document.createElement("details");
    details.className = "encounter-card-details";
    const summary = document.createElement("summary");
    const heading = document.createElement("span");
    heading.className = "encounter-card-heading";
    const caseId = document.createElement("strong");
    caseId.textContent = card.case_id;
    const status = document.createElement("span");
    status.className = `encounter-support ${card.support?.observable ? "is-observable" : "is-insufficient"}`;
    status.textContent = encounterSupportLabel(card.support?.status);
    heading.append(caseId, status);
    const headline = document.createElement("span");
    headline.className = "encounter-card-headline";
    headline.textContent = `${labels.dcpa} ${metricWithUnit(card.prediction?.dcpa_nm, labels.nauticalMiles)} · ${labels.actualDistance} ${metricWithUnit(
      card.observation?.continuous_min_distance_nm,
      labels.nauticalMiles,
    )}`;
    summary.append(heading, headline);

    const body = document.createElement("div");
    body.className = "encounter-card-body";
    const metrics = document.createElement("div");
    metrics.className = "encounter-card-metrics";
    metrics.append(
      encounterMetric(labels.tcpa, metricWithUnit(card.prediction?.tcpa_min, labels.minutes)),
      encounterMetric(labels.sourceSkew, metricWithUnit(card.synchronized_t0?.source_state_skew_s, labels.seconds)),
      encounterMetric(labels.timeError, metricWithUnit(card.observation?.closest_time_abs_error_s, labels.seconds)),
      encounterMetric(
        labels.coverage,
        `${metricWithUnit(card.coverage?.common_coverage_duration_s, labels.seconds)} (${card.coverage?.common_coverage_fraction == null ? "-" : formatPercent(
          Number(card.coverage.common_coverage_fraction) * 100,
        )})`,
      ),
      encounterMetric(
        labels.samples,
        `${formatNumber(card.coverage?.common_sample_count)} / ${formatNumber(card.coverage?.scheduled_sample_count)}`,
      ),
      encounterMetric(labels.maxGap, metricWithUnit(card.coverage?.max_uncovered_gap_s, labels.seconds)),
    );
    const sensitivity = document.createElement("p");
    sensitivity.className = "encounter-sensitivity";
    sensitivity.textContent = `${labels.sensitivity}: ${["10_s", "30_s", "60_s"]
      .map((key) => metricWithUnit(card.grid_sensitivity?.[key]?.min_distance_nm, labels.nauticalMiles))
      .join(" · ")}`;
    const geometryTitle = document.createElement("h3");
    geometryTitle.textContent = labels.futureGeometry;
    const boundary = document.createElement("p");
    boundary.className = "encounter-boundary";
    boundary.textContent = labels.boundary;
    body.append(metrics, sensitivity, geometryTitle, encounterGeometrySvg(card), boundary);
    details.append(summary, body);
    details.addEventListener("toggle", () => {
      if (details.open) setStatus(`${card.case_id}: ${encounterSupportLabel(card.support?.status)}`);
    });
    container.append(details);
  }
}

function renderEvidenceCards() {
  const container = document.getElementById("evidenceCards");
  const section = document.getElementById("evidenceCardsSection");
  container.innerHTML = "";
  const cards = state.evidenceCards?.cards || [];
  section.hidden = cards.length === 0;
  if (!cards.length) {
    const empty = document.createElement("p");
    empty.className = "empty-note";
    empty.textContent = currentCopy().evidenceCardsEmpty;
    container.append(empty);
    return;
  }
  if (state.evidenceCards?.schema_version === "review-v9.encounter-evidence-card.v1") {
    renderEncounterEvidenceCards(container, cards);
    return;
  }
  for (const card of cards) {
    const button = document.createElement("button");
    button.className = "evidence-card-button";
    const header = document.createElement("span");
    header.className = "card-header";
    const title = document.createElement("span");
    title.className = "card-title";
    title.textContent = `${card.rank}. ${card.cell_id}`;
    const score = document.createElement("span");
    score.className = "card-score";
    score.textContent = formatNumber(card.screening_score);
    header.append(title, score);

    const type = document.createElement("span");
    type.className = "card-type";
    type.textContent = translateText(card.hotspot_type);

    const counts = document.createElement("span");
    counts.className = "card-counts";
    const countLabels = currentCopy().cardCounts;
    counts.textContent = `${formatNumber(card.counts?.encounter_episodes)} ${countLabels.episodes} · ${formatNumber(
      card.counts?.backtest_supported_episodes,
    )} ${countLabels.supported} · ${formatNumber(card.counts?.corroborated_anomaly_candidates)} ${countLabels.anomalies}`;

    const focus = document.createElement("span");
    focus.className = "card-focus";
    focus.textContent = translateText(card.review_focus);

    button.append(header, type, counts, focus);
    button.addEventListener("click", () => {
      const feature = hotspotFeatureByCell(card.cell_id);
      const layer = state.layers.get("fused_risk_hotspots");
      if (layer && !state.map.hasLayer(layer)) layer.addTo(state.map);
      if (feature) {
        const bounds = L.geoJSON(feature).getBounds();
        state.map.fitBounds(bounds.pad(0.45), { maxZoom: 13 });
      }
      setStatus(
        currentCopy().status.card(
          card,
          formatNumber(card.counts?.encounter_episodes),
          formatNumber(card.counts?.backtest_supported_episodes),
        ),
      );
    });
    container.append(button);
  }
}

function renderReproducibility() {
  const container = document.getElementById("reproPanel");
  const summary = state.summary || {};
  const manifest = state.manifest || {};
  const dateRange = summary.date_range || manifest.date_range || {};
  const rows = currentCopy().reproRows.map(([label, value]) => {
    if (value === "date_range") return [label, `${dateRange.start || "-"} ${state.language === "zh" ? "至" : "to"} ${dateRange.end || "-"}`];
    if (value === "source") return [label, localizedManifestValue("source") || "-"];
    if (value === "workflow_role") return [label, localizedManifestValue("workflow_role") || "-"];
    if (value === "study_area") return [label, localizedManifestValue("study_area") || translateText(summary.study_area) || "-"];
    return [label, value];
  });
  container.innerHTML = "";
  for (const [label, value] of rows) {
    const row = document.createElement("div");
    row.className = "repro-row";
    const labelEl = document.createElement("span");
    labelEl.className = "repro-label";
    labelEl.textContent = label;
    const valueEl = document.createElement("span");
    valueEl.className = "repro-value";
    valueEl.textContent = value;
    row.append(labelEl, valueEl);
    container.append(row);
  }
}

function renderLayerControls() {
  const container = document.getElementById("layerControls");
  container.innerHTML = "";
  for (const layer of state.manifest.layers) {
    const data = state.layerData.get(layer.id);
    const label = document.createElement("label");
    label.className = "layer-toggle";
    const input = document.createElement("input");
    input.type = "checkbox";
    const mapLayer = state.layers.get(layer.id);
    input.checked = Boolean(mapLayer && state.map.hasLayer(mapLayer));
    input.addEventListener("change", () => {
      const mapLayer = state.layers.get(layer.id);
      if (!mapLayer) return;
      if (input.checked) {
        mapLayer.addTo(state.map);
      } else {
        state.map.removeLayer(mapLayer);
      }
    });
    const textWrap = document.createElement("span");
    textWrap.className = "layer-text";
    const name = document.createElement("span");
    name.className = "layer-name";
    name.textContent = currentCopy().layerLabels[layer.id] || layer.label;
    const role = document.createElement("span");
    role.className = "layer-role";
    role.textContent = currentCopy().layerRoles[layer.id] || currentCopy().layerFallback;
    textWrap.append(name, role);
    const count = document.createElement("span");
    count.className = "layer-count";
    count.textContent = data ? formatNumber(countFeatures(data)) : "-";
    label.append(input, textWrap, count);
    label.style.borderLeft = `4px solid ${LAYER_COLORS[layer.id] || "#2f80ed"}`;
    container.append(label);
  }
}

function renderCases() {
  const container = document.getElementById("caseList");
  const section = document.getElementById("casesSection");
  container.innerHTML = "";
  const data = state.layerData.get("case_tracks");
  const hasCases = Boolean(data && Array.isArray(data.features) && data.features.length);
  section.hidden = !hasCases;
  if (!hasCases) return;
  data.features.forEach((feature) => {
    const props = feature.properties || {};
    const button = document.createElement("button");
    button.className = "case-button";
    const title = document.createElement("span");
    title.className = "case-title";
    title.textContent = props.case_id || "case";
    const meta = document.createElement("span");
    meta.className = "case-meta";
    meta.textContent = `${formatNumber(props.anomaly_count || 0)} ${currentCopy().caseMeta}`;
    button.append(title, meta);
    button.addEventListener("click", () => {
      const layer = state.layers.get("case_tracks");
      if (!state.map.hasLayer(layer)) layer.addTo(state.map);
      const bounds = L.geoJSON(feature).getBounds();
      state.map.fitBounds(bounds.pad(0.2), { maxZoom: 13 });
      setStatus(currentCopy().status.case(props));
    });
    container.append(button);
  });
}

async function loadDataset(datasetId, options = {}) {
  const dataset = state.catalog?.datasets?.find((item) => item.id === datasetId);
  if (!dataset) throw new Error(`Unknown dataset: ${datasetId}`);
  const sequence = ++state.loadSequence;
  setStatus(`${currentCopy().status.switching}: ${localizedDatasetLabel(dataset)}`);

  const manifestData = options.manifest || (await loadJson(`${DATA_DIR}${dataset.manifest}`));
  const companion = manifestData.companion_data || {};
  const summaryPromise = companion.summary ? loadJson(`${DATA_DIR}${companion.summary}`) : Promise.resolve(null);
  const evidenceCardPath = companion.encounter_evidence_cards || companion.evidence_cards;
  const cardsPromise = evidenceCardPath
    ? loadJson(`${DATA_DIR}${evidenceCardPath}`).catch(() => null)
    : Promise.resolve(null);
  const layerEntries = await Promise.all(
    (manifestData.layers || []).map(async (layerMeta) => [layerMeta, await loadJson(`${DATA_DIR}${layerMeta.path}`)]),
  );
  const [summaryData, evidenceCardsData] = await Promise.all([summaryPromise, cardsPromise]);
  if (sequence !== state.loadSequence) return;

  for (const mapLayer of state.layers.values()) {
    if (state.map.hasLayer(mapLayer)) state.map.removeLayer(mapLayer);
  }
  state.map.closePopup();
  state.layers.clear();
  state.layerData.clear();
  state.activeDatasetId = datasetId;
  state.manifest = manifestData;
  state.summary = summaryData;
  state.evidenceCards = evidenceCardsData;
  window.localStorage.setItem(DATASET_STORAGE_KEY, datasetId);

  for (const [layerMeta, data] of layerEntries) {
    state.layerData.set(layerMeta.id, data);
    const mapLayer = createGeoJsonLayer(layerMeta.id, data);
    state.layers.set(layerMeta.id, mapLayer);
    if (DEFAULT_VISIBLE.has(layerMeta.id)) mapLayer.addTo(state.map);
  }

  renderStaticText();
  renderDynamicSections();
  state.map.invalidateSize();
  const densityLayer = state.layers.get("traffic_density");
  const densityBounds = densityLayer?.getBounds();
  if (densityBounds?.isValid()) {
    state.map.fitBounds(densityBounds, { padding: [20, 20] });
  } else {
    const view = state.manifest.map_view || {};
    state.map.setView(view.center || FALLBACK_CENTER, view.zoom || FALLBACK_ZOOM);
  }
  window.setTimeout(() => state.map.invalidateSize(), 250);
  setStatus(`${currentCopy().status.loaded}: ${localizedDatasetLabel(activeDatasetMeta())}`);
}

async function init() {
  initLanguageControls();
  setStatus(currentCopy().status.preparing);

  state.catalog = await loadJson(`${DATA_DIR}datasets.json`).catch(() => ({
    default_dataset: "sf_bay",
    datasets: [{ id: "sf_bay", manifest: "manifest.json", label: { en: "San Francisco Bay", zh: "旧金山湾" } }],
  }));
  const availableIds = new Set(state.catalog.datasets.map((dataset) => dataset.id));
  if (!availableIds.has(state.activeDatasetId)) state.activeDatasetId = state.catalog.default_dataset;
  const initialDataset = activeDatasetMeta();
  const initialManifest = await loadJson(`${DATA_DIR}${initialDataset.manifest}`);
  const initialView = initialManifest.map_view || {};

  state.map = L.map("map", {
    zoomControl: true,
    preferCanvas: true,
  }).setView(initialView.center || FALLBACK_CENTER, initialView.zoom || FALLBACK_ZOOM);

  const basemap = initialManifest.basemap || {};
  state.basemap = L.tileLayer(basemap.url || "https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution:
      basemap.attribution ||
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(state.map);

  await loadDataset(state.activeDatasetId, { manifest: initialManifest });
}

init().catch((error) => {
  console.error(error);
  setStatus(error.message);
});
