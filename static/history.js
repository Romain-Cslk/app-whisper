const calendarNode = document.getElementById("history-calendar");
const loadingNode = document.getElementById("history-loading");
const emptyNode = document.getElementById("history-empty");
const rangeNode = document.getElementById("history-range");
const searchInput = document.getElementById("history-search");
const prevButton = document.getElementById("history-prev");
const nextButton = document.getElementById("history-next");
const todayButton = document.getElementById("history-today");
const themeButton = document.getElementById("toggle-theme");
const detailPanel = document.getElementById("history-detail");
const detailOverlay = document.getElementById("history-overlay");
const detailClose = document.getElementById("history-detail-close");
const detailTitle = document.getElementById("history-detail-title");
const detailMeta = document.getElementById("history-detail-meta");
const detailSources = document.getElementById("history-detail-sources");
const detailArtifacts = document.getElementById("history-detail-artifacts");
const detailContent = document.getElementById("history-detail-content");

const HOUR_HEIGHT = 64;
const DAY_NAMES = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"];
let weekStart = startOfWeek(new Date());
let refreshTimer = null;
let loadSequence = 0;

function startOfWeek(value) {
  const date = new Date(value);
  date.setHours(0, 0, 0, 0);
  const day = (date.getDay() + 6) % 7;
  date.setDate(date.getDate() - day);
  return date;
}

function addDays(value, days) {
  const date = new Date(value);
  date.setDate(date.getDate() + days);
  return date;
}

function sameDay(left, right) {
  return left.getFullYear() === right.getFullYear() &&
    left.getMonth() === right.getMonth() &&
    left.getDate() === right.getDate();
}

function formatRange(start) {
  const end = addDays(start, 6);
  const formatter = new Intl.DateTimeFormat("fr-FR", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
  const short = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short" });
  if (start.getFullYear() === end.getFullYear() && start.getMonth() === end.getMonth()) {
    return `${start.getDate()} – ${formatter.format(end)}`;
  }
  return `${short.format(start)} – ${formatter.format(end)}`;
}

function weekBounds() {
  return {
    start: new Date(weekStart),
    end: addDays(weekStart, 7),
  };
}

function updateThemeButton() {
  if (!themeButton) return;
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  themeButton.textContent = dark ? "☀️ Mode clair" : "🌙 Mode sombre";
}

function toggleTheme() {
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  const next = dark ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("theme", next);
  updateThemeButton();
}

function buildCalendar(records) {
  calendarNode.replaceChildren();
  const today = new Date();

  const corner = document.createElement("div");
  corner.className = "history-corner";
  calendarNode.appendChild(corner);

  const cells = Array.from({ length: 7 }, () => Array(24));
  for (let dayIndex = 0; dayIndex < 7; dayIndex += 1) {
    const date = addDays(weekStart, dayIndex);
    const header = document.createElement("div");
    header.className = "history-day-header" + (sameDay(date, today) ? " is-today" : "");
    header.style.gridColumn = String(dayIndex + 2);
    const label = document.createElement("strong");
    label.textContent = DAY_NAMES[dayIndex];
    const dateLabel = document.createElement("span");
    dateLabel.textContent = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short" }).format(date);
    header.append(label, dateLabel);
    calendarNode.appendChild(header);
  }

  for (let hour = 0; hour < 24; hour += 1) {
    const hourLabel = document.createElement("div");
    hourLabel.className = "history-hour-label";
    hourLabel.style.gridRow = String(hour + 2);
    hourLabel.textContent = `${String(hour).padStart(2, "0")}:00`;
    calendarNode.appendChild(hourLabel);

    for (let dayIndex = 0; dayIndex < 7; dayIndex += 1) {
      const date = addDays(weekStart, dayIndex);
      const cell = document.createElement("div");
      cell.className = "history-hour-cell" + (sameDay(date, today) ? " is-today" : "");
      cell.style.gridColumn = String(dayIndex + 2);
      cell.style.gridRow = String(hour + 2);
      cells[dayIndex][hour] = cell;
      calendarNode.appendChild(cell);
    }
  }

  for (const record of records) {
    const start = new Date(record.created_at);
    if (Number.isNaN(start.getTime())) continue;
    const normalizedDayIndex = Math.round((new Date(start.getFullYear(), start.getMonth(), start.getDate()) - weekStart) / 86400000);
    if (normalizedDayIndex < 0 || normalizedDayIndex > 6) continue;

    const hour = start.getHours();
    const cell = cells[normalizedDayIndex][hour];
    if (!cell) continue;

    const event = document.createElement("button");
    event.type = "button";
    event.className = "history-event";
    event.dataset.status = record.status || "done";
    event.style.top = `${Math.round((start.getMinutes() / 60) * HOUR_HEIGHT) + 3}px`;

    const finished = record.finished_at ? new Date(record.finished_at) : null;
    const processingMinutes = finished && !Number.isNaN(finished.getTime())
      ? Math.max(0, (finished - start) / 60000)
      : 0;
    event.style.height = `${Math.max(34, Math.min(92, processingMinutes * 1.2 || 42))}px`;

    const time = document.createElement("span");
    time.className = "history-event-time";
    time.textContent = new Intl.DateTimeFormat("fr-FR", { hour: "2-digit", minute: "2-digit" }).format(start);
    const title = document.createElement("span");
    title.className = "history-event-title";
    title.textContent = record.title || "Transcription";
    const source = document.createElement("span");
    source.className = "history-event-source";
    source.textContent = (record.source_files || []).map((item) => item.name).filter(Boolean).join(", ") ||
      `${(record.artifacts || []).length} fichier(s) TXT`;
    event.append(time, title, source);
    event.addEventListener("click", () => openDetail(record.id));
    cell.appendChild(event);
  }
}

async function loadWeek() {
  const sequence = ++loadSequence;
  loadingNode.hidden = false;
  loadingNode.textContent = "Chargement de l'historique…";
  emptyNode.hidden = true;
  calendarNode.hidden = true;
  rangeNode.textContent = formatRange(weekStart);

  const { start, end } = weekBounds();
  const params = new URLSearchParams({
    start: start.toISOString(),
    end: end.toISOString(),
    limit: "1000",
  });
  const query = searchInput.value.trim();
  if (query) params.set("q", query);

  try {
    const response = await fetch(`/api/history?${params.toString()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`Historique indisponible (${response.status}).`);
    const payload = await response.json();
    if (sequence !== loadSequence) return;
    const records = Array.isArray(payload.records) ? payload.records : [];
    buildCalendar(records);
    loadingNode.hidden = true;
    emptyNode.hidden = records.length > 0;
    calendarNode.hidden = false;
  } catch (error) {
    if (sequence !== loadSequence) return;
    loadingNode.textContent = error?.message || String(error);
    emptyNode.hidden = true;
    calendarNode.hidden = true;
  }
}

function closeDetail() {
  detailPanel.classList.remove("is-open");
  detailPanel.setAttribute("aria-hidden", "true");
  detailOverlay.hidden = true;
}

async function showArtifact(record, artifact, linkNode) {
  detailArtifacts.querySelectorAll(".history-artifact-link").forEach((node) => node.classList.remove("is-active"));
  if (linkNode) linkNode.classList.add("is-active");
  detailContent.textContent = "Chargement du content…";
  try {
    const response = await fetch(
      `/api/history/${encodeURIComponent(record.id)}/content/${encodeURIComponent(artifact.name)}`,
      { cache: "no-store" },
    );
    if (!response.ok) throw new Error(`Contenu indisponible (${response.status}).`);
    detailContent.textContent = await response.text();
  } catch (error) {
    detailContent.textContent = error?.message || String(error);
  }
}

async function openDetail(jobId) {
  detailPanel.classList.add("is-open");
  detailPanel.setAttribute("aria-hidden", "false");
  detailOverlay.hidden = false;
  detailTitle.textContent = "Chargement…";
  detailMeta.textContent = "";
  detailSources.replaceChildren();
  detailArtifacts.replaceChildren();
  detailContent.textContent = "Chargement du contenu…";

  try {
    const response = await fetch(`/api/history/${encodeURIComponent(jobId)}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`Historique indisponible (${response.status}).`);
    const record = await response.json();
    const created = new Date(record.created_at);
    detailTitle.textContent = record.title || "Transcription";
    detailMeta.textContent = [
      new Intl.DateTimeFormat("fr-FR", { dateStyle: "full", timeStyle: "short" }).format(created),
      record.use_api === true ? "API OpenAI" : record.use_api === false ? "Local" : "Mode inconnu",
      record.model || null,
      record.lang || null,
      record.status || null,
    ].filter(Boolean).join(" · ");

    for (const source of record.source_files || []) {
      const chip = document.createElement("span");
      chip.className = "history-chip";
      chip.textContent = source.name || "Fichier source";
      detailSources.appendChild(chip);
    }

    const artifacts = Array.isArray(record.artifacts) ? record.artifacts : [];
    for (const artifact of artifacts) {
      const wrapper = document.createElement("span");
      const openButton = document.createElement("button");
      openButton.type = "button";
      openButton.className = "history-artifact-link";
      openButton.textContent = `Ouvrir ${artifact.name}`;
      openButton.addEventListener("click", () => showArtifact(record, artifact, openButton));
      const download = document.createElement("a");
      download.className = "history-artifact-link";
      download.href = `/api/history/${encodeURIComponent(record.id)}/download/${encodeURIComponent(artifact.name)}`;
      download.textContent = "Télécharger";
      wrapper.append(openButton, document.createTextNode(" "), download);
      detailArtifacts.appendChild(wrapper);
    }

    const preferred = artifacts.find((artifact) => artifact.kind === "transcription") || artifacts[0];
    if (preferred) {
      const preferredButton = Array.from(detailArtifacts.querySelectorAll("button")).find((button) =>
        button.textContent.includes(preferred.name),
      );
      await showArtifact(record, preferred, preferredButton);
    } else {
      detailContent.textContent = "Aucun TXT archivé pour ce traitement.";
    }
  } catch (error) {
    detailTitle.textContent = "Erreur";
    detailContent.textContent = error?.message || String(error);
  }
}

function scheduleSearch() {
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = setTimeout(loadWeek, 250);
}

prevButton.addEventListener("click", () => {
  weekStart = addDays(weekStart, -7);
  loadWeek();
});
nextButton.addEventListener("click", () => {
  weekStart = addDays(weekStart, 7);
  loadWeek();
});
todayButton.addEventListener("click", () => {
  weekStart = startOfWeek(new Date());
  loadWeek();
});
searchInput.addEventListener("input", scheduleSearch);
detailClose.addEventListener("click", closeDetail);
detailOverlay.addEventListener("click", closeDetail);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeDetail();
});
if (themeButton) themeButton.addEventListener("click", toggleTheme);

updateThemeButton();
loadWeek();
