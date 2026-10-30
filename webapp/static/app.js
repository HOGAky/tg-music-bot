/* tg-music-bot Mini App: плеер, очередь, караоке, медиатека. */
(() => {
"use strict";

const tg = window.Telegram && window.Telegram.WebApp;
const INIT_DATA = (tg && tg.initData) || "";
if (tg) { tg.ready(); tg.expand(); tg.setHeaderColor && tg.setHeaderColor("bg_color"); }

const $ = (id) => document.getElementById(id);
const audio = $("audio");
const content = $("content");

function haptic(style = "light") {
  try { tg && tg.HapticFeedback && tg.HapticFeedback.impactOccurred(style); } catch (e) {}
}
function notify(text) {
  if (tg && tg.showPopup) tg.showPopup({ message: text });
  else toast(text);
}
let toastTimer = null;
function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 2200);
}

async function api(path, opts = {}) {
  const headers = Object.assign({ "X-Telegram-Init-Data": INIT_DATA }, opts.headers || {});
  if (opts.body) headers["Content-Type"] = "application/json";
  const r = await fetch(path, Object.assign({}, opts, { headers }));
  if (r.status === 401) { toast("Открой плеер через Telegram"); throw new Error("401"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));
  return data;
}
const apiPost = (path, body) =>
  api(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });
const apiDelete = (path) => api(path, { method: "DELETE" });

const fmt = (s) => {
  if (!s && s !== 0) return "";
  s = Math.max(0, Math.floor(s));
  return s < 3600
    ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`
    : `${Math.floor(s / 3600)}:${String(Math.floor(s % 3600 / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
};

// ---------- состояние ----------
let queue = [];            // текущая очередь треков
let qIndex = -1;
let repeatMode = "off";    // off -> all -> one
let shuffle = false;
let currentTab = "search";
let searchResults = [];
let lastQuery = "";
let lrcLines = null;       // [[sec, text, domEl], ...] караоке
let lrcTrackId = null;
let playlistsCache = [];

const cur = () => (qIndex >= 0 && qIndex < queue.length) ? queue[qIndex] : null;

// ---------- карточки треков ----------
function trackCard(t, list, opts = {}) {
  const card = document.createElement("div");
  card.className = "card" + (cur() && cur().id === t.id ? " playing" : "");
  card.dataset.trackId = t.id;

  const cover = document.createElement("div");
  cover.className = "card-cover";
  cover.textContent = "🎵";

  const body = document.createElement("div");
  body.className = "card-body";
  const title = document.createElement("div");
  title.className = "card-title";
  title.textContent = t.title || "Без названия";
  const artist = document.createElement("div");
  artist.className = "card-artist";
  artist.textContent = (t.artist || "—") + (t.duration ? " · " + fmt(t.duration) : "");
  body.append(title, artist);

  const acts = document.createElement("div");
  acts.className = "card-actions";
  const mk = (icon, title_, fn) => {
    const b = document.createElement("button");
    b.className = "icon-btn"; b.title = title_; b.textContent = icon;
    b.onclick = (e) => { e.stopPropagation(); fn(); };
    return b;
  };
  acts.append(
    mk("▶️", "Слушать", () => playFrom(list, list.indexOf(t))),
    mk("⬇️", "Скачать MP3", () => downloadTrack(t)),
    mk("📩", "В Telegram", () => sendTg(t)),
    mk("📝", "Текст", () => openLyrics(t)),
    (function () {
      const b = mk(t.in_fav ? "❤️" : "🤍", "Избранное", async () => {
        try {
          const r = await apiPost(`/api/favorites/${t.id}`);
          t.in_fav = r.in_fav;
          b.textContent = r.in_fav ? "❤️" : "🤍";
          b.classList.toggle("on", r.in_fav);
          haptic("light");
        } catch (e) { toast("Не вышло: " + e.message); }
      });
      b.className = "icon-btn fav-btn" + (t.in_fav ? " on" : "");
      return b;
    })(),
    mk("📁", "В плейлист", () => openSheet(t)),
  );
  if (opts.onRemove) acts.append(mk("✖️", "Убрать", () => opts.onRemove(t)));

  card.append(cover, body, acts);
  body.onclick = () => playFrom(list, list.indexOf(t));
  return card;
}

function renderTrackList(list, opts = {}) {
  content.innerHTML = "";
  if (opts.header) content.append(opts.header);
  if (!list.length) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = opts.emptyText || "Пусто";
    content.append(e);
    return;
  }
  list.forEach((t) => content.append(trackCard(t, list, opts.cardOpts || {})));
}

// ---------- вкладки ----------
document.querySelectorAll(".tab").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    currentTab = b.dataset.tab;
    haptic("light");
    loadTab();
  };
});

function loadTab() {
  if (currentTab === "search") renderSearch();
  else if (currentTab === "playlists") renderPlaylists();
  else if (currentTab === "favorites") renderFavorites();
  else if (currentTab === "top") renderTop();
}

// ---------- поиск ----------
function renderSearch() {
  content.innerHTML = "";
  if (!lastQuery) {
    const e = document.createElement("div");
    e.className = "empty";
    e.innerHTML = "Введи название трека или вставь ссылку:<br>Spotify · YouTube · SoundCloud · Яндекс Музыка";
    content.append(e);
    return;
  }
  renderTrackList(searchResults, { emptyText: "Ничего не найдено" });
}

async function doSearch() {
  const q = $("searchInput").value.trim();
  if (!q) return;
  lastQuery = q;
  content.innerHTML = '<div class="spinner">Ищу…</div>';
  try {
    const data = await api("/api/search?q=" + encodeURIComponent(q));
    searchResults = data.tracks || [];
    if (currentTab === "search") renderSearch();
    if (searchResults.length > 1) toast(`Найдено: ${searchResults.length}`);
  } catch (e) {
    content.innerHTML = `<div class="empty">Ошибка поиска: ${e.message}</div>`;
  }
}
$("searchBtn").onclick = doSearch;
$("searchInput").addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });

// ---------- избранное ----------
async function renderFavorites() {
  content.innerHTML = '<div class="spinner">Загружаю…</div>';
  try {
    const data = await api("/api/favorites");
    const list = data.tracks || [];
    renderTrackList(list, { emptyText: "В избранном пусто. Жми 🤍 на треке." });
  } catch (e) { content.innerHTML = `<div class="empty">${e.message}</div>`; }
}

// ---------- топ ----------
async function renderTop() {
  content.innerHTML = '<div class="spinner">Загружаю…</div>';
  try {
    const data = await api("/api/top");
    content.innerHTML = "";
    const h1 = document.createElement("div");
    h1.className = "section-title"; h1.textContent = "🏆 Топ бота";
    content.append(h1);
    (data.top || []).forEach((t, i) => {
      const c = trackCard(t, data.top);
      const b = c.querySelector(".card-artist");
      b.textContent = `${i + 1}. ${b.textContent} · ${t.plays}▶`;
      content.append(c);
    });
    if (data.recent && data.recent.length) {
      const h2 = document.createElement("div");
      h2.className = "section-title"; h2.textContent = "🕘 Недавно слушал";
      content.append(h2);
      data.recent.forEach((t) => content.append(trackCard(t, data.recent)));
    }
    if (!data.top.length && !(data.recent || []).length)
      content.innerHTML = '<div class="empty">Пока никто ничего не слушал</div>';
  } catch (e) { content.innerHTML = `<div class="empty">${e.message}</div>`; }
}

// ---------- плейлисты ----------
async function renderPlaylists() {
  content.innerHTML = '<div class="spinner">Загружаю…</div>';
  try {
    const data = await api("/api/playlists");
    playlistsCache = data.playlists || [];
    content.innerHTML = "";
    const row = document.createElement("div");
    row.className = "search-row";
    const inp = document.createElement("input");
    inp.id = "newPlInput"; inp.placeholder = "Название нового плейлиста";
    inp.style.cssText = "flex:1;padding:12px 14px;border:none;border-radius:12px;background:var(--bg2);color:var(--text);font-size:15px;outline:none";
    const btn = document.createElement("button");
    btn.className = "icon-btn"; btn.textContent = "➕"; btn.title = "Создать";
    btn.onclick = async () => {
      const name = inp.value.trim();
      if (!name) return;
      try { await apiPost("/api/playlists", { name }); haptic("medium"); renderPlaylists(); }
      catch (e) { toast(e.message); }
    };
    row.append(inp, btn);
    content.append(row);

    if (!playlistsCache.length) {
      const e = document.createElement("div");
      e.className = "empty"; e.textContent = "Плейлистов пока нет";
      content.append(e);
      return;
    }
    playlistsCache.forEach((p) => {
      const card = document.createElement("div");
      card.className = "card";
      card.innerHTML = `<div class="card-cover">🎵</div>
        <div class="card-body"><div class="card-title"></div>
        <div class="card-artist"></div></div>`;
      card.querySelector(".card-title").textContent = p.name;
      card.querySelector(".card-artist").textContent = `${p.count} треков`;
      const acts = document.createElement("div");
      acts.className = "card-actions";
      const play = document.createElement("button");
      play.className = "icon-btn"; play.textContent = "▶️"; play.title = "Слушать всё";
      play.onclick = () => openPlaylist(p.id, true);
      const del = document.createElement("button");
      del.className = "icon-btn"; del.textContent = "🗑"; del.title = "Удалить";
      del.onclick = async () => {
        try { await apiDelete(`/api/playlists/${p.id}`); haptic("medium"); renderPlaylists(); }
        catch (e) { toast(e.message); }
      };
      acts.append(play, del);
      card.append(acts);
      card.querySelector(".card-body").onclick = () => openPlaylist(p.id, false);
      content.append(card);
    });
  } catch (e) { content.innerHTML = `<div class="empty">${e.message}</div>`; }
}

async function openPlaylist(id, autoplay) {
  content.innerHTML = '<div class="spinner">Загружаю…</div>';
  try {
    const data = await api(`/api/playlists/${id}`);
    const header = document.createElement("div");
    header.innerHTML = `<div class="section-title">🎵 ${data.name} · ${data.tracks.length} треков</div>`;
    const back = document.createElement("button");
    back.className = "btn ghost"; back.textContent = "⬅️ К плейлистам";
    back.onclick = renderPlaylists;
    header.append(back);
    renderTrackList(data.tracks, {
      header, emptyText: "Плейлист пуст",
      cardOpts: {
        onRemove: async (t) => {
          try {
            await apiDelete(`/api/playlists/${id}/tracks/${t.id}`);
            openPlaylist(id, false);
          } catch (err) { toast(err.message); }
        },
      },
    });
    if (autoplay && data.tracks.length) playFrom(data.tracks, 0);
  } catch (e) { content.innerHTML = `<div class="empty">${e.message}</div>`; }
}

function openSheet(track) {
  const list = $("sheetList");
  list.innerHTML = "";
  const items = playlistsCache.length ? Promise.resolve(playlistsCache)
    : api("/api/playlists").then((d) => (playlistsCache = d.playlists || []));
  items.then((pls) => {
    list.innerHTML = "";
    if (!pls.length) {
      list.innerHTML = '<div class="empty">Сначала создай плейлист во вкладке «Плейлисты»</div>';
    }
    pls.forEach((p) => {
      const it = document.createElement("div");
      it.className = "sheet-item";
      it.textContent = `🎵 ${p.name}`;
      it.onclick = async () => {
        try {
          await apiPost(`/api/playlists/${p.id}/tracks`, { track_id: track.id });
          toast("Добавлено в «" + p.name + "»");
          haptic("medium");
        } catch (e) { toast(e.message); }
        $("sheet").classList.add("hidden");
      };
      list.append(it);
    });
  });
  $("sheet").classList.remove("hidden");
}
$("sheetCancel").onclick = () => $("sheet").classList.add("hidden");
$("sheet").addEventListener("click", (e) => {
  if (e.target === $("sheet")) $("sheet").classList.add("hidden");
});

// ---------- воспроизведение ----------
function playFrom(list, i) {
  if (i < 0 || i >= list.length) return;
  queue = list.slice();
  qIndex = i;
  startCurrent();
}

function startCurrent() {
  const t = cur();
  if (!t) return;
  audio.src = "/api/stream/" + t.id + (INIT_DATA ? "?_data=" + encodeURIComponent(INIT_DATA) : "");
  audio.play().catch((e) => console.warn("play()", e));
  apiPost("/api/play/" + t.id).catch(() => {});
  updatePlayerUI(t);
  document.querySelectorAll(".card.playing").forEach((c) => c.classList.remove("playing"));
  const el = document.querySelector(`.card[data-track-id="${t.id}"]`);
  if (el) el.classList.add("playing");
  haptic("light");
}

function updatePlayerUI(t) {
  $("miniPlayer").classList.remove("hidden");
  $("mpTitle").textContent = t.title || "—";
  $("mpArtist").textContent = t.artist || "";
  $("fpTitle").textContent = t.title || "—";
  $("fpArtist").textContent = t.artist || "";
  if (lrcTrackId !== t.id) { lrcLines = null; lrcTrackId = null; }
  renderQueue();
}

function renderQueue() {
  const box = $("fpQueue");
  box.innerHTML = "";
  if (!queue.length) return;
  const h = document.createElement("div");
  h.className = "section-title";
  h.textContent = `Очередь · ${queue.length}`;
  box.append(h);
  queue.forEach((t, i) => {
    const it = document.createElement("div");
    it.className = "sheet-item" + (i === qIndex ? " active" : "");
    if (i === qIndex) it.style.color = "var(--link)";
    it.textContent = `${i + 1}. ${t.artist ? t.artist + " — " : ""}${t.title}`;
    it.onclick = () => { qIndex = i; startCurrent(); };
    box.append(it);
  });
}

function nextTrack(auto) {
  if (!queue.length) return;
  if (auto && repeatMode === "one") { audio.currentTime = 0; audio.play(); return; }
  if (shuffle && queue.length > 1) {
    let n;
    do { n = Math.floor(Math.random() * queue.length); } while (n === qIndex);
    qIndex = n;
  } else {
    qIndex++;
    if (qIndex >= queue.length) {
      if (repeatMode === "all" || !auto) qIndex = 0;
      else { qIndex = queue.length - 1; return; }
    }
  }
  startCurrent();
}
function prevTrack() {
  if (audio.currentTime > 3 || qIndex <= 0) { audio.currentTime = 0; return; }
  qIndex--; startCurrent();
}

function togglePlay() {
  if (audio.paused) audio.play().catch(() => {});
  else audio.pause();
  haptic("light");
}
$("mpPlay").onclick = togglePlay;
$("fpPlay").onclick = togglePlay;
$("mpNext").onclick = () => nextTrack(false);
$("fpNext").onclick = () => nextTrack(false);
$("fpPrev").onclick = prevTrack;
$("mpInfo").onclick = () => $("fullPlayer").classList.remove("hidden");
$("mpExpand").onclick = () => $("fullPlayer").classList.remove("hidden");
$("fpCollapse").onclick = () => $("fullPlayer").classList.add("hidden");

$("fpShuffle").onclick = (e) => {
  shuffle = !shuffle;
  e.target.classList.toggle("on", shuffle);
  haptic("light");
};
$("fpRepeat").onclick = (e) => {
  repeatMode = repeatMode === "off" ? "all" : repeatMode === "all" ? "one" : "off";
  e.target.textContent = repeatMode === "one" ? "🔂" : "🔁";
  e.target.classList.toggle("on", repeatMode !== "off");
  haptic("light");
};
$("fpVolume").oninput = (e) => { audio.volume = e.target.value / 100; };

audio.addEventListener("play", () => { $("mpPlay").textContent = "⏸"; $("fpPlay").textContent = "⏸"; });
audio.addEventListener("pause", () => { $("mpPlay").textContent = "▶️"; $("fpPlay").textContent = "▶️"; });
audio.addEventListener("ended", () => nextTrack(true));
audio.addEventListener("error", () => {
  if (audio.src) toast("Не удалось воспроизвести поток");
});
audio.addEventListener("loadedmetadata", () => {
  $("fpDur").textContent = fmt(audio.duration);
});

// перемотка: выставление currentTime заставляет браузер шлёт Range -> HTTP 206
let seeking = false;
$("fpSeek").addEventListener("input", () => { seeking = true; });
$("fpSeek").addEventListener("change", (e) => {
  if (isFinite(audio.duration) && audio.duration > 0)
    audio.currentTime = (e.target.value / 1000) * audio.duration;
  seeking = false;
});

audio.addEventListener("timeupdate", () => {
  const d = audio.duration;
  if (isFinite(d) && d > 0) {
    $("mpBar").style.width = (audio.currentTime / d * 100) + "%";
    if (!seeking) $("fpSeek").value = Math.round(audio.currentTime / d * 1000);
    $("fpCur").textContent = fmt(audio.currentTime);
  }
  syncKaraoke();
});

// ---------- караоке ----------
async function openLyrics(t) {
  haptic("light");
  $("lmTitle").textContent = t.title || "Текст";
  $("lmArtist").textContent = t.artist || "";
  const body = $("lyricsBody");
  body.innerHTML = '<div class="spinner">Ищу текст…</div>';
  $("lyricsModal").classList.remove("hidden");
  lrcLines = null;
  try {
    const data = await api(`/api/lyrics/${t.id}`);
    body.innerHTML = "";
    if (data.synced && data.synced.length) {
      lrcTrackId = t.id;
      lrcLines = data.synced.map(([sec, text]) => {
        const el = document.createElement("div");
        el.className = "lrc-line";
        el.textContent = text;
        body.append(el);
        return [sec, text, el];
      });
      syncKaraoke();
    } else if (data.plain) {
      const p = document.createElement("div");
      p.className = "lyrics-plain";
      p.textContent = data.plain;
      body.append(p);
    } else {
      body.innerHTML = '<div class="empty">Текст не найден</div>';
    }
  } catch (e) {
    body.innerHTML = '<div class="empty">Текст для этого трека не нашёлся 🤷</div>';
  }
}
$("fpLyrics").onclick = () => { const t = cur(); if (t) openLyrics(t); };
$("lmClose").onclick = () => $("lyricsModal").classList.add("hidden");

let lrcActive = -1;
function syncKaraoke() {
  if (!lrcLines || !lrcLines.length) return;
  const t = audio.currentTime;
  let idx = -1;
  for (let i = 0; i < lrcLines.length; i++) {
    if (lrcLines[i][0] <= t) idx = i; else break;
  }
  if (idx === lrcActive) return;
  if (lrcActive >= 0 && lrcLines[lrcActive]) lrcLines[lrcActive][2].classList.remove("active");
  lrcActive = idx;
  if (idx >= 0) {
    const el = lrcLines[idx][2];
    el.classList.add("active");
    el.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

// ---------- действия карточки ----------
function downloadTrack(t) {
  haptic("medium");
  const sep = INIT_DATA ? "?_data=" + encodeURIComponent(INIT_DATA) : "";
  window.open("/api/download/" + t.id + sep, "_blank");
}

async function sendTg(t) {
  haptic("medium");
  try {
    await apiPost(`/api/send_tg/${t.id}`);
    notify("📩 Трек отправлен в чат с ботом");
  } catch (e) {
    notify("Не удалось отправить: " + e.message);
  }
}

// ---------- радио ----------
$("fpRadio").onclick = async () => {
  const t = cur();
  if (!t) return;
  haptic("light");
  try {
    const data = await api(`/api/radio/${t.id}`);
    if (data.tracks && data.tracks.length) {
      toast(`📻 Радио: ${data.tracks.length} похожих`);
      queue = queue.slice(0, qIndex + 1).concat(data.tracks);
      renderQueue();
    } else toast("Похожих не нашлось");
  } catch (e) { toast(e.message); }
};

// ---------- старт ----------
loadTab();
})();
