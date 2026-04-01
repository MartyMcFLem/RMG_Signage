/* ═══════════════════════════════════════════════════
   RMG Signage — Main Application JS
   ═══════════════════════════════════════════════════ */

let _files     = [];
let isDragging = false;
let dragSrcEl  = null;
let toastTimer = null;
let _currentPage = 'dashboard';

const VIDEO_EXT = /\.(mp4|avi|mkv|mov|webm|m4v)$/i;
const GIF_EXT   = /\.gif$/i;
const isVideo = f => VIDEO_EXT.test(f);
const isGif   = f => GIF_EXT.test(f);

// ── Navigation ────────────────────────────────────────
function goTo(page) {
  _currentPage = page;
  document.querySelectorAll('.page-section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
  const sec = document.getElementById('page-' + page);
  const link = document.querySelector(`.nav-link[data-page="${page}"]`);
  if (sec) sec.classList.add('active');
  if (link) link.classList.add('active');
  window.location.hash = page;
  const mg = document.getElementById('navGroupMedias');
  if (page === 'medias' || page === 'playlists') {
    mg.classList.add('open');
  }
  if (page === 'playlists') loadPlaylists();
  if (page === 'pages') loadPages();
  document.getElementById('sidebarNav').classList.remove('open');
  document.getElementById('navOverlay').classList.remove('open');
}
function toggleNav() {
  document.getElementById('sidebarNav').classList.toggle('open');
  document.getElementById('navOverlay').classList.toggle('open');
}
function toggleMediasNav() {
  document.getElementById('navGroupMedias').classList.toggle('open');
}

// ── Toast ─────────────────────────────────────────────
function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 3500);
}

// ── Dashboard ─────────────────────────────────────────
async function loadDashboard() {
  try {
    const _safe = url => fetch(url).then(r => r.json()).catch(() => ({}));
    const [status, storage, lic] = await Promise.all([
      _safe('/api/status'),
      _safe('/api/storage'),
      _safe('/api/license'),
    ]);

    const playerEl = document.getElementById('dashPlayerStatus');
    const playerSub = document.getElementById('dashPlayerSub');
    const isRunning = status.player_running;
    if (isRunning) {
      playerEl.innerHTML = '<span class="status-dot on"></span>Actif';
      playerSub.textContent = status.media_count + ' medias charges';
    } else {
      playerEl.innerHTML = '<span class="status-dot off"></span>Arrete';
      playerSub.textContent = '';
    }

    document.getElementById('dashMediaCount').textContent = status.media_count;
    document.getElementById('dashMediaSub').textContent = status.media_count > 1 ? 'fichiers' : 'fichier';

    const tier = lic.tier || 'none';
    document.getElementById('dashLicense').textContent = tier.charAt(0).toUpperCase() + tier.slice(1);
    const qMb = lic.media_quota_mb || 0;
    document.getElementById('dashLicenseSub').textContent = qMb >= 1024 ? (qMb/1024).toFixed(0) + ' Go alloues' : qMb + ' Mo alloues';

    if (status.serial) {
      document.getElementById('navSerialFull').textContent = status.serial;
    }
    if (status.version) {
      document.getElementById('navVersion').textContent = status.version;
    }

    const used = storage.used_mb ?? 0;
    const total = storage.total_mb ?? 0;
    const pct = storage.usage_percent ?? 0;
    const fmtSize = mb => mb >= 1024 ? (mb/1024).toFixed(1)+' Go' : mb+' Mo';
    document.getElementById('storageUsed').textContent = total > 0 ? fmtSize(used) + ' utilises' : '--';
    document.getElementById('storageTotal').textContent = total > 0 ? fmtSize(total) : '--';
    document.getElementById('storagePct').textContent = total > 0 ? pct + '% utilise' : '';
    const fill = document.getElementById('storageFill');
    fill.style.width = Math.min(pct, 100) + '%';
    fill.classList.remove('warn','crit');
    if (pct >= 90) fill.classList.add('crit');
    else if (pct >= 70) fill.classList.add('warn');

    const mCount = storage.media_count || 0;
    const maxF = storage.max_files || 0;
    const filesEl = document.getElementById('storageFiles');
    if (maxF > 0) {
      filesEl.textContent = mCount + ' / ' + maxF + ' fichiers';
      if (mCount >= maxF) filesEl.style.color = 'var(--red)';
      else filesEl.style.color = '';
    } else {
      filesEl.textContent = mCount + ' fichiers';
    }

    const dot = document.getElementById('playerDot');
    const pLabel = document.getElementById('playerLabel');
    const pTitle = document.getElementById('playerTitle');
    if (isRunning) {
      dot.className = 'player-now-dot on';
      pLabel.textContent = 'En lecture';
      pTitle.textContent = status.media_count + ' media' + (status.media_count > 1 ? 's' : '') + ' en rotation';
    } else {
      dot.className = 'player-now-dot off';
      pLabel.textContent = 'Lecteur';
      pTitle.textContent = 'Arrete';
    }
  } catch(e) {}
}

// ── Config ────────────────────────────────────────────
async function loadConfig() {
  const cfg = await fetch('/api/config').then(r => r.json());
  document.getElementById('duration').value  = cfg.image_duration;
  document.getElementById('shuffle').checked = cfg.shuffle;
  document.getElementById('loop').checked    = cfg.loop;
  document.getElementById('darkMode').checked = !!cfg.dark_mode;
  applyDarkMode(!!cfg.dark_mode);
  const rotEl = document.getElementById('rotation');
  if (rotEl) rotEl.value = String(cfg.rotation ?? 0);
  refreshNowPlaying(cfg);
}
function refreshNowPlaying(cfg) {
  const playAllBtn = document.getElementById('playAllBtn');
  const pTitle = document.getElementById('playerTitle');
  const pLabel = document.getElementById('playerLabel');
  if (cfg.single_file_mode && cfg.selected_file) {
    if (pTitle) pTitle.textContent = cfg.selected_file;
    if (pLabel) pLabel.textContent = 'Fichier unique';
    playAllBtn.style.display = 'flex';
  } else {
    playAllBtn.style.display = 'none';
  }
}
async function updateConfig() {
  const payload = {
    image_duration: parseInt(document.getElementById('duration').value),
    shuffle: document.getElementById('shuffle').checked,
    loop:    document.getElementById('loop').checked,
    dark_mode: document.getElementById('darkMode').checked,
    rotation: parseInt(document.getElementById('rotation').value),
  };
  await fetch('/api/config', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(payload),
  });
  applyDarkMode(payload.dark_mode);
  showToast('Parametres enregistres');
  loadConfig();
  loadGallery();
}
function applyDarkMode(on) {
  document.body.classList.toggle('dark', on);
}

// ── Gallery ───────────────────────────────────────────
async function loadGallery() {
  if (isDragging) return;
  const [files, cfg] = await Promise.all([
    fetch('/api/files').then(r => r.json()),
    fetch('/api/config').then(r => r.json()),
  ]);
  let sorted = [...files];
  if (cfg.file_order && cfg.file_order.length) {
    const om = Object.fromEntries(cfg.file_order.map((f,i)=>[f,i]));
    sorted.sort((a,b) => {
      const ai = om[a] ?? 1e9, bi = om[b] ?? 1e9;
      return ai !== bi ? ai - bi : a.localeCompare(b);
    });
  }
  _files = sorted;
  const n = files.length;
  document.getElementById('galleryCount').textContent = n + ' fichier' + (n>1?'s':'');
  document.getElementById('headerCount').textContent = n;
  document.getElementById('shuffleNote').style.display = cfg.shuffle ? 'block' : 'none';
  document.getElementById('orderNote').style.display = cfg.shuffle ? 'none' : 'block';
  refreshNowPlaying(cfg);
  const draggable = !cfg.shuffle;
  document.getElementById('gallery').innerHTML = sorted.map((file, i) => {
    const sel       = cfg.single_file_mode && cfg.selected_file === file;
    const video     = isVideo(file);
    const gif       = isGif(file);
    const customDur = cfg.file_durations?.[file] ?? '';
    const defDur    = cfg.image_duration;
    const safeFile  = file.replace(/"/g, '&quot;');
    return `
      <div class="gallery-item${sel?' selected':''}${draggable?' draggable':''}"
           data-filename="${safeFile}"
           ${draggable ? 'draggable="true" ondragstart="dragStart(event)"' : ''}
           ondragover="dragOver(event)" ondragleave="dragLeave(event)"
           ondrop="drop(event)" ondragend="dragEnd(event)">
        <div class="img-wrap">
          <img src="/media/${encodeURIComponent(file)}"
               onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 1 1%22><rect fill=%22%23e4e6ea%22 width=%221%22 height=%221%22/></svg>'">
          ${draggable ? '<div class="drag-pip">&#8942;&#8942;</div>' : ''}
          <div class="item-overlay">
            <div class="item-actions">
              <button class="ia-btn ia-play" onclick="_gPlay(${i})" title="Afficher uniquement">&#9654;</button>
              <button class="ia-btn ia-del"  onclick="_gDelete(${i})" title="Supprimer">&#10005;</button>
            </div>
          </div>
        </div>
        <div class="item-foot">
          <span class="type-badge ${video?'badge-video':gif?'badge-gif':'badge-img'}">${video?'VIDEO':gif?'GIF':'IMG'}</span>
          <span class="item-name">${file}</span>
          ${!video ? `<div class="dur-pill" onclick="event.stopPropagation()"><input type="number" value="${customDur}" placeholder="${defDur}" min="1" max="300" title="Duree" onchange="_gSetDur(${i},this.value)" onclick="event.stopPropagation()"><span>s</span></div>` : ''}
        </div>
      </div>`;
  }).join('');
}
const _gPlay   = i => playSingle(_files[i]);
const _gDelete = i => deleteFile(_files[i]);
const _gSetDur = (i, v) => setFileDuration(_files[i], v);

// ── Upload ────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const uploadZone = document.getElementById('uploadZone');
  const fileInput  = document.getElementById('fileInput');
  uploadZone.addEventListener('dragover', e => { e.preventDefault(); uploadZone.classList.add('drag-on'); });
  uploadZone.addEventListener('dragleave', e => { if (!uploadZone.contains(e.relatedTarget)) uploadZone.classList.remove('drag-on'); });
  uploadZone.addEventListener('drop', e => { e.preventDefault(); uploadZone.classList.remove('drag-on'); if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files); });
  fileInput.addEventListener('change', () => { if (fileInput.files.length) uploadFiles(fileInput.files); });
});

async function uploadFiles(files) {
  const prog = document.getElementById('uploadProgress');
  prog.style.display = 'block';
  const fd = new FormData();
  for (const f of files) fd.append('files', f);
  const res = await fetch('/', { method: 'POST', body: fd });
  prog.style.display = 'none';
  document.getElementById('fileInput').value = '';
  if (res.ok) {
    if (res.url && res.url.includes('error=files')) {
      showToast('Nombre maximum de fichiers atteint -- augmentez votre licence');
    } else if (res.url && res.url.includes('error=quota')) {
      showToast('Espace de stockage insuffisant -- augmentez votre licence');
    } else {
      loadGallery(); loadDashboard();
      showToast(files.length + ' fichier' + (files.length>1?'s':'') + ' envoye' + (files.length>1?'s':''));
    }
  }
}

// ── Logo ──────────────────────────────────────────────
async function uploadLogo(files) {
  if (!files || !files.length) return;
  const fd = new FormData(); fd.append('logo', files[0]);
  document.getElementById('logoInput').value = '';
  const res = await fetch('/api/logo', { method: 'POST', body: fd });
  if (res.ok) {
    const v = '?v=' + Date.now();
    document.getElementById('siteLogo').src = '/static/logo.png' + v;
    document.getElementById('siteLogo').style.display = 'block';
    const pv = document.getElementById('logoPreview');
    if (pv) { pv.src = '/static/logo.png' + v; pv.style.display = 'block'; }
    showToast('Logo mis a jour');
  }
}
async function removeLogo() {
  if (!confirm('Supprimer le logo ?')) return;
  const res = await fetch('/api/logo', { method: 'DELETE' });
  if (res.ok) {
    document.getElementById('siteLogo').style.display = 'none';
    const pv = document.getElementById('logoPreview');
    if (pv) pv.style.display = 'none';
    showToast('Logo supprime');
  }
}

// ── Drag & drop gallery ───────────────────────────────
function dragStart(e) { isDragging=true; dragSrcEl=e.currentTarget; e.dataTransfer.effectAllowed='move'; e.currentTarget.classList.add('dragging'); }
function dragOver(e)  { e.preventDefault(); e.dataTransfer.dropEffect='move'; if(e.currentTarget!==dragSrcEl) e.currentTarget.classList.add('drag-over'); }
function dragLeave(e) { e.currentTarget.classList.remove('drag-over'); }
function dragEnd()    { isDragging=false; document.querySelectorAll('.gallery-item').forEach(el=>el.classList.remove('drag-over','dragging')); }
function drop(e) {
  e.preventDefault(); e.stopPropagation();
  const target = e.currentTarget; target.classList.remove('drag-over');
  if (!dragSrcEl || dragSrcEl === target) { isDragging=false; return; }
  const items = [...document.querySelectorAll('.gallery-item')];
  const si = items.indexOf(dragSrcEl), di = items.indexOf(target);
  const g = document.getElementById('gallery');
  if (si < di) g.insertBefore(dragSrcEl, target.nextSibling);
  else g.insertBefore(dragSrcEl, target);
  isDragging = false;
  saveOrder();
}
async function saveOrder() {
  const order = [...document.querySelectorAll('.gallery-item')].map(el => el.dataset.filename);
  _files = order;
  await fetch('/api/order', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({order}) });
  showToast('Ordre mis a jour');
}

// ── File actions ──────────────────────────────────────
async function setFileDuration(filename, value) {
  const duration = value ? parseInt(value) : null;
  await fetch('/api/file-duration/'+encodeURIComponent(filename), { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({duration}) });
  showToast(duration ? filename+' : '+duration+'s' : 'Duree par defaut');
}
async function playSingle(filename) {
  try {
    const res = await fetch('/api/play-single/'+encodeURIComponent(filename), {method:'POST'});
    const data = await res.json();
    showToast(data.message || 'OK');
    loadGallery(); loadConfig(); loadDashboard();
  } catch(e) { showToast('Erreur : ' + e.message); }
}
async function playAll() {
  try {
    const res = await fetch('/api/play-all', {method:'POST'});
    const data = await res.json();
    showToast(data.message || 'OK');
    loadGallery(); loadConfig(); loadDashboard();
  } catch(e) { showToast('Erreur : ' + e.message); }
}
async function deleteFile(filename) {
  if (!confirm('Supprimer '+filename+' ?')) return;
  try {
    await fetch('/api/delete/'+encodeURIComponent(filename), {method:'DELETE'});
    loadGallery(); loadDashboard();
    showToast(filename+' supprime');
  } catch(e) { showToast('Erreur : ' + e.message); }
}
async function control(action) {
  try {
    const res = await fetch('/api/control/'+action, {method:'POST'});
    const data = await res.json();
    showToast(data.message || 'OK');
    loadConfig(); loadDashboard();
  } catch(e) {
    showToast('Erreur : ' + e.message);
  }
}

// ── License ───────────────────────────────────────────
async function loadLicense() {
  try {
    const lic = await fetch('/api/license').then(r => r.json());
    const tier = lic.tier || 'none';
    const badge = document.getElementById('licenseBadge');
    badge.textContent = tier.charAt(0).toUpperCase() + tier.slice(1);
    badge.className = 'license-badge badge-' + tier;
    const qMb = lic.media_quota_mb || 0;
    document.getElementById('licenseQuota').textContent =
      qMb >= 1024 ? (qMb/1024).toFixed(0)+' Go' : qMb+' Mo';
    const maxF = lic.max_files || 0;
    document.getElementById('licenseMaxFiles').textContent =
      maxF > 0 ? '/ ' + maxF + ' fichiers max' : '/ illimite';
    const preview = document.getElementById('licenseKeyPreview');
    if (lic.key_preview) {
      preview.textContent = 'Cle : ' + lic.key_preview;
      if (lic.activated) preview.textContent += ' (active le ' + lic.activated.substring(0,10) + ')';
    } else {
      preview.textContent = 'Aucune cle activee';
    }
  } catch(e) {}
}
async function activateLicense() {
  const input = document.getElementById('licenseKeyInput');
  const key = input.value.trim();
  if (!key) { showToast('Entrez une cle de licence'); return; }
  try {
    const res = await fetch('/api/license/activate', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({key}),
    });
    const data = await res.json();
    if (data.success) {
      showToast(data.message);
      input.value = '';
      loadLicense(); loadDashboard();
    } else {
      showToast('Erreur : ' + data.message);
    }
  } catch(e) {
    showToast('Erreur reseau');
  }
}

// ── Git update ────────────────────────────────────────
async function loadGitStatus() {
  const alertEl = document.getElementById('dashUpdateAlert');
  try {
    const data = await fetch('/api/update/status').then(r => r.json());
    const el = document.getElementById('gitInfo');
    if (data.success) {
      if (data.up_to_date === false) {
        el.innerHTML = '<span class="git-badge git-outdated">Mise a jour disponible</span>';
        alertEl.classList.add('show');
      } else if (data.up_to_date === true) {
        el.innerHTML = '<span class="git-badge git-uptodate">A jour</span>';
        document.getElementById('navVersion').textContent = 'v' + (data.commit || '?');
        alertEl.classList.remove('show');
      } else {
        el.innerHTML = '<span style="color:var(--text-2);font-size:12px">Statut inconnu</span>';
        alertEl.classList.remove('show');
      }
    } else {
      el.innerHTML = '<span style="color:var(--red);font-size:12px">'+(data.message||'git non disponible')+'</span>';
      alertEl.classList.remove('show');
    }
  } catch(e) {
    document.getElementById('gitInfo').innerHTML = '<span style="color:var(--text-2)">git non disponible</span>';
    alertEl.classList.remove('show');
  }
}
async function runUpdate() {
  const btn = document.getElementById('updateBtn');
  btn.disabled = true; btn.textContent = 'Mise a jour...';
  try {
    const data = await fetch('/api/update', {method:'POST'}).then(r => r.json());
    if (data.success) {
      if (data.updated) {
        btn.textContent = 'Redemarrage...';
        setTimeout(() => location.reload(), 4000);
        return;
      } else { showToast('Deja a jour'); loadGitStatus(); }
    } else { showToast('Erreur : '+data.message); }
  } catch(e) { showToast('Erreur reseau'); }
  btn.disabled = false; btn.textContent = 'Mettre a jour';
}

// ── Playlists ─────────────────────────────────────────
let _plModalMode = 'create';
let _plModalId = null;
let _playlists = [];

const _plActivate = i => activatePlaylist(_playlists[i].id);
const _plEdit     = i => openPlModal(_playlists[i].id);
const _plDelete   = i => deletePlaylist(_playlists[i].id, _playlists[i].name);

async function loadPlaylists() {
  try {
    const data = await fetch('/api/playlists').then(r => r.json());
    const pls = data.playlists || [];
    const active = data.active_playlist;

    const banner = document.getElementById('plActiveBanner');
    if (active) {
      const aPl = pls.find(p => p.id === active);
      if (aPl) {
        document.getElementById('plActiveName').textContent = aPl.name;
        banner.style.display = 'flex';
      } else { banner.style.display = 'none'; }
    } else { banner.style.display = 'none'; }

    const list = document.getElementById('plList');
    if (!pls.length) {
      list.innerHTML = '<div class="pl-empty">Aucune playlist. Cliquez sur "+ Nouvelle playlist" pour commencer.</div>';
      return;
    }
    _playlists = pls;
    list.innerHTML = pls.map((pl, idx) => {
      const isActive = pl.id === active;
      const n = (pl.files || []).length;
      return `
        <div class="pl-item${isActive ? ' active-pl' : ''}">
          <div class="pl-icon">${isActive ? '&#9654;' : '&#9776;'}</div>
          <div class="pl-info">
            <div class="pl-name">${pl.name}</div>
            <div class="pl-meta">${n} media${n>1?'s':''}${isActive ? ' &middot; En lecture' : ''}</div>
          </div>
          <div class="pl-actions">
            ${isActive
              ? '<button class="btn btn-ghost btn-sm" onclick="deactivatePlaylist()">Stop</button>'
              : `<button class="btn btn-blue btn-sm" onclick="_plActivate(${idx})">Lire</button>`}
            <button class="btn btn-ghost btn-sm" onclick="_plEdit(${idx})">Editer</button>
            <button class="btn btn-ghost btn-sm" style="color:var(--red)" onclick="_plDelete(${idx})">Suppr.</button>
          </div>
        </div>`;
    }).join('');
  } catch(e) {}
}

async function deletePlaylist(id, name) {
  if (!confirm('Supprimer la playlist "' + name + '" ?')) return;
  await fetch('/api/playlists/' + id, {method: 'DELETE'});
  loadPlaylists();
  showToast('Playlist supprimee');
}

async function activatePlaylist(id) {
  try {
    const res = await fetch('/api/playlists/' + id + '/activate', {method: 'POST'});
    const data = await res.json();
    showToast(data.message || 'Playlist activee');
    loadPlaylists(); loadDashboard(); loadConfig();
  } catch(e) { showToast('Erreur : ' + e.message); }
}

async function deactivatePlaylist() {
  try {
    await fetch('/api/playlists/deactivate', {method: 'POST'});
    showToast('Lecture de tous les fichiers');
    loadPlaylists(); loadDashboard(); loadConfig();
  } catch(e) { showToast('Erreur : ' + e.message); }
}

// ── Playlist Modal ────────────────────────────────────
async function openPlModal(editId) {
  const allFiles = await fetch('/api/files').then(r => r.json());
  let selectedFiles = new Set();
  let name = '';

  if (editId) {
    _plModalMode = 'edit';
    _plModalId = editId;
    const plData = await fetch('/api/playlists/' + editId).then(r => r.json());
    name = plData.name || '';
    selectedFiles = new Set(plData.files || []);
    document.getElementById('plModalTitle').textContent = 'Modifier la playlist';
    document.getElementById('plModalSave').textContent = 'Enregistrer';
  } else {
    _plModalMode = 'create';
    _plModalId = null;
    document.getElementById('plModalTitle').textContent = 'Nouvelle playlist';
    document.getElementById('plModalSave').textContent = 'Creer';
  }

  document.getElementById('plModalName').value = name;

  const picker = document.getElementById('plModalPicker');
  if (!allFiles.length) {
    picker.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:30px;color:var(--text-3)">Aucun fichier importe</div>';
  } else {
    picker.innerHTML = allFiles.map(f => {
      const sel = selectedFiles.has(f) ? ' selected' : '';
      const safeF = f.replace(/"/g, '&quot;');
      return `<div class="media-pick-item${sel}" data-file="${safeF}" onclick="togglePickItem(this)">
        <img src="/media/${encodeURIComponent(f)}" onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 1 1%22><rect fill=%22%23333%22 width=%221%22 height=%221%22/></svg>'">
        <div class="media-pick-check"></div>
        <div class="media-pick-name">${f}</div>
      </div>`;
    }).join('');
  }

  document.getElementById('plModal').classList.add('show');
}

function togglePickItem(el) {
  el.classList.toggle('selected');
}

function closePlModal() {
  document.getElementById('plModal').classList.remove('show');
  _plModalId = null;
}

async function savePlModal() {
  const name = document.getElementById('plModalName').value.trim();
  if (!name) { showToast('Entrez un nom'); return; }
  const items = document.querySelectorAll('#plModalPicker .media-pick-item.selected');
  const files = [...items].map(el => el.dataset.file);

  if (_plModalMode === 'edit' && _plModalId) {
    await fetch('/api/playlists/' + _plModalId, {
      method: 'PUT',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({name, files}),
    });
    showToast('Playlist enregistree (' + files.length + ' medias)');
  } else {
    await fetch('/api/playlists', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({name, files}),
    });
    showToast('Playlist "' + name + '" creee');
  }
  closePlModal();
  loadPlaylists();
}

// ── Pages de signage ──────────────────────────────────
let _pages           = [];
let _editPageId      = null;   // null = création, string = édition
let _pbWidgets       = [];     // widgets en cours d'édition
let _pbSelected      = null;   // id du widget sélectionné dans le canvas
let _pbDrag          = null;   // état du drag {wId, mode, handle, startX, startY, origX, origY, origW, origH, cw, ch}
let _bgImageUrl      = null;   // URL de l'image de fond de la page en cours d'édition
let _builderPlaylists = [];    // playlists chargées pour le widget média
let _builderFiles     = [];    // fichiers importés chargés pour le widget média
const SNAP_GRID = 2.5;         // taille de grille (écran % de la taille totale)
const snapVal = v => Math.round(v / SNAP_GRID) * SNAP_GRID;
const WIDGET_COLORS = {
  background:'#6366f1', clock:'#2563eb', text:'#16a34a',
  weather:'#ea580c', media:'#0891b2', ticker:'#7c3aed',
};

async function loadPages() {
  try {
    const data = await fetch('/api/pages').then(r => r.json());
    _pages = data.pages || [];
    const active = data.active_page || null;

    const banner = document.getElementById('pageActiveBanner');
    if (active) {
      const ap = _pages.find(p => p.id === active);
      if (ap) {
        document.getElementById('pageActiveName').textContent = ap.name;
        banner.style.display = 'flex';
      } else { banner.style.display = 'none'; }
    } else { banner.style.display = 'none'; }

    const list = document.getElementById('pagesList');
    if (!_pages.length) {
      list.innerHTML = '<div class="pl-empty">Aucune page. Cliquez sur "+ Nouvelle page" pour commencer.</div>';
      return;
    }
    list.innerHTML = _pages.map((p, i) => {
      const isActive = p.id === active;
      const rotLabel = p.rotation ? ` &middot; ${p.rotation}°` : '';
      return `
      <div class="pl-item${isActive ? ' active-pl' : ''}">
        <div class="pl-icon">${isActive ? '&#9654;' : '&#9718;'}</div>
        <div class="pl-info">
          <div class="pl-name">${p.name}</div>
          <div class="pl-meta">${(p.widgets||[]).length} widget${(p.widgets||[]).length>1?'s':''}${rotLabel}${isActive ? ' &middot; Lecture seule' : ''}</div>
        </div>
        <div class="pl-actions">
          ${isActive
            ? '<button class="btn btn-ghost btn-sm" onclick="deactivatePage()">Stop</button>'
            : `<button class="btn btn-blue btn-sm" onclick="activatePage('${p.id}')">Lire seule</button>`}
          <button class="btn btn-ghost btn-sm" onclick="previewPageById('${p.id}')">Aperçu</button>
          <button class="btn btn-ghost btn-sm" onclick="openPageBuilder('${p.id}')">Editer</button>
          <button class="btn btn-ghost btn-sm" style="color:var(--red)" onclick="deletePage('${p.id}','${p.name.replace(/'/g,'&#39;')}')">Suppr.</button>
        </div>
      </div>`;
    }).join('');
  } catch(e) {}
}

async function activatePage(id) {
  try {
    const res = await fetch('/api/pages/' + id + '/activate', {method: 'POST'});
    const data = await res.json();
    showToast(data.message || 'Page active');
    loadPages(); loadDashboard();
  } catch(e) { showToast('Erreur : ' + e.message); }
}

async function deactivatePage() {
  try {
    await fetch('/api/pages/deactivate', {method: 'POST'});
    showToast('Lecture normale reprise');
    loadPages(); loadDashboard();
  } catch(e) { showToast('Erreur : ' + e.message); }
}

async function deletePage(id, name) {
  if (!confirm('Supprimer la page "' + name + '" ?')) return;
  await fetch('/api/pages/' + id, {method:'DELETE'});
  loadPages();
  showToast('Page supprimée');
}

function previewPage() {
  if (!_editPageId) { showToast('Enregistrez d\'abord la page.'); return; }
  previewPageById(_editPageId);
}
function previewPageById(id) {
  window.open('/signage/' + id, '_blank', 'width=960,height=540');
}

/* ── Builder open/close ──────────────────────────── */
function openPageBuilder(pageId) {
  _editPageId = pageId || null;
  _pbWidgets = [];
  _pbSelected = null;
  _bgImageUrl = null;

  if (pageId) {
    const p = _pages.find(p => p.id === pageId);
    if (p) {
      document.getElementById('pbName').value = p.name;
      document.getElementById('pbBgColor').value = p.bg_color || '#1a1a2e';
      document.getElementById('pbRotation').value = String(p.rotation || 0);
      _pbWidgets = JSON.parse(JSON.stringify(p.widgets || []));
      _bgImageUrl = p.bg_image || null;
    }
  } else {
    document.getElementById('pbName').value = '';
    document.getElementById('pbBgColor').value = '#1a1a2e';
    document.getElementById('pbRotation').value = '0';
  }
  const removeBtn = document.getElementById('pbBgImageRemoveBtn');
  if (removeBtn) removeBtn.style.display = _bgImageUrl ? '' : 'none';

  // Sync canvas bg with color picker
  document.getElementById('pbBgColor').oninput = () => renderCanvas();
  // Pre-fetch playlists et fichiers pour le widget média
  fetch('/api/playlists').then(r => r.json()).then(data => {
    _builderPlaylists = data.playlists || [];
  }).catch(() => { _builderPlaylists = []; });
  fetch('/api/files').then(r => r.json()).then(data => {
    _builderFiles = data || [];
  }).catch(() => { _builderFiles = []; });
  document.getElementById('pageBuilder').style.display = 'block';
  renderCanvas();
}

function closePageBuilder() {
  document.getElementById('pageBuilder').style.display = 'none';
  _editPageId = null;
  _pbWidgets = [];
  _pbSelected = null;
  _bgImageUrl = null;
}

async function uploadPageBgImage(files) {
  if (!files || !files.length) return;
  if (!_editPageId) { showToast('Enregistrez d\'abord la page'); return; }
  const fd = new FormData();
  fd.append('image', files[0]);
  document.getElementById('pbBgImageInput').value = '';
  try {
    const res = await fetch('/api/pages/' + _editPageId + '/bg-image', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.success) {
      _bgImageUrl = data.url + '?v=' + Date.now();
      renderCanvas();
      document.getElementById('pbBgImageRemoveBtn').style.display = '';
      showToast('Image de fond mise à jour');
    } else {
      showToast('Erreur : ' + data.message);
    }
  } catch(e) { showToast('Erreur réseau'); }
}

async function removePageBgImage() {
  if (!_editPageId) return;
  try {
    await fetch('/api/pages/' + _editPageId + '/bg-image', { method: 'DELETE' });
    _bgImageUrl = null;
    renderCanvas();
    document.getElementById('pbBgImageRemoveBtn').style.display = 'none';
    showToast('Image de fond supprimée');
  } catch(e) { showToast('Erreur réseau'); }
}

/* ── Save ────────────────────────────────────────── */
async function savePageBuilder() {
  const name = document.getElementById('pbName').value.trim();
  if (!name) { showToast('Entrez un nom de page'); return; }
  const bg_color = document.getElementById('pbBgColor').value;
  const rotation = parseInt(document.getElementById('pbRotation').value) || 0;
  const payload = {name, bg_color, rotation, widgets: _pbWidgets};
  try {
    if (_editPageId) {
      await fetch('/api/pages/' + _editPageId, {
        method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload),
      });
      showToast('Page mise à jour');
    } else {
      const res = await fetch('/api/pages', {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload),
      });
      const data = await res.json();
      _editPageId = data.page?.id || null;
      showToast('Page créée');
    }
    loadPages();
  } catch(e) { showToast('Erreur : ' + e.message); }
}

/* ── Widget management ───────────────────────────── */
const _widgetDefaults = {
  background: {x:0,y:0,w:100,h:100, config:{color:'#1a1a2e'}},
  clock:      {x:10,y:5,w:80,h:30, config:{font_size:90,color:'#ffffff',show_seconds:false,show_date:true,date_format:'full',date_font_size:28,date_color:'#aaaacc'}},
  text:       {x:10,y:40,w:80,h:20, config:{text:'Votre texte ici',font_size:48,color:'#ffffff',align:'center',bold:false,italic:false}},
  weather:    {x:5,y:55,w:45,h:40, config:{lat:48.85,lon:2.35,city:'Paris',unit:'celsius',temp_font_size:72,icon_font_size:60,desc_font_size:22,city_font_size:22,color:'#ffffff'}},
  media:      {x:55,y:5,w:40,h:55, config:{fit:'contain',duration:8}},
  ticker:     {x:0,y:88,w:100,h:12, config:{rss_url:'',font_size:28,color:'#ffffff',bg_color:'rgba(0,0,0,0.6)',speed:60}},
};

function addWidget(type) {
  const def = _widgetDefaults[type] || {x:10,y:10,w:30,h:20,config:{}};
  const w = {
    id: 'w' + Date.now(),
    type,
    x: def.x, y: def.y, w: def.w, h: def.h,
    config: JSON.parse(JSON.stringify(def.config)),
  };
  _pbWidgets.push(w);
  renderCanvas();
  selectWidget(w.id);
}

function deleteSelectedWidget() {
  if (!_pbSelected) return;
  _pbWidgets = _pbWidgets.filter(w => w.id !== _pbSelected);
  _pbSelected = null;
  renderCanvas();
  document.getElementById('pbProps').textContent = 'Sélectionnez un widget sur le canvas.';
  document.getElementById('pbPropsTitle').textContent = 'Propriétés';
}

function selectWidget(wId) {
  _pbSelected = wId;
  renderCanvas();
  renderWidgetProps();
}

/* ── Canvas ──────────────────────────────────────── */
// 8 resize handles: corners + edges
const HANDLE_DEFS = [
  ['nw', 'top:0;left:0;transform:translate(-50%,-50%);cursor:nwse-resize'],
  ['n',  'top:0;left:50%;transform:translate(-50%,-50%);cursor:ns-resize'],
  ['ne', 'top:0;right:0;transform:translate(50%,-50%);cursor:nesw-resize'],
  ['e',  'top:50%;right:0;transform:translate(50%,-50%);cursor:ew-resize'],
  ['se', 'bottom:0;right:0;transform:translate(50%,50%);cursor:nwse-resize'],
  ['s',  'bottom:0;left:50%;transform:translate(-50%,50%);cursor:ns-resize'],
  ['sw', 'bottom:0;left:0;transform:translate(-50%,50%);cursor:nesw-resize'],
  ['w',  'top:50%;left:0;transform:translate(-50%,-50%);cursor:ew-resize'],
];

function renderCanvas() {
  const canvas = document.getElementById('pbCanvas');
  const bg = document.getElementById('pbBgColor').value;
  canvas.style.backgroundColor = bg;
  canvas.style.backgroundImage = [
    'linear-gradient(rgba(255,255,255,0.07) 1px, transparent 1px)',
    'linear-gradient(90deg, rgba(255,255,255,0.07) 1px, transparent 1px)',
  ].join(',');
  canvas.style.backgroundSize = `${SNAP_GRID}% ${SNAP_GRID}%`;
  canvas.style.overflow = 'visible';

  // Image de fond : div absolue sous les widgets
  let bgImgEl = canvas.querySelector('.pb-bg-img');
  if (_bgImageUrl) {
    if (!bgImgEl) {
      bgImgEl = document.createElement('div');
      bgImgEl.className = 'pb-bg-img';
      bgImgEl.style.cssText = 'position:absolute;inset:0;pointer-events:none;background-size:cover;background-position:center;background-repeat:no-repeat;';
      canvas.prepend(bgImgEl);
    }
    bgImgEl.style.backgroundImage = `url(${_bgImageUrl})`;
  } else if (bgImgEl) {
    bgImgEl.remove();
  }

  [...canvas.querySelectorAll('.pb-widget')].forEach(e => e.remove());

  const labels = {clock:'⏰ Horloge',text:'💬 Texte',weather:'🌤 Météo',
                  media:'🖼 Média',ticker:'📰 Ticker',background:'🎨 Fond'};

  for (const w of _pbWidgets) {
    if (w.type === 'background') continue;
    const color = WIDGET_COLORS[w.type] || '#888';
    const selected = w.id === _pbSelected;

    const el = document.createElement('div');
    el.className = 'pb-widget';
    el.dataset.wid = w.id;
    el.style.cssText = [
      'position:absolute;overflow:visible;',
      `left:${w.x}%;top:${w.y}%;width:${w.w}%;height:${w.h}%;`,
      `background:${color}22;`,
      `border:2px ${selected ? 'solid' : 'dashed'} ${color};`,
      'border-radius:4px;display:flex;align-items:center;justify-content:center;',
      'font-size:11px;text-align:center;cursor:move;box-sizing:border-box;',
      `color:${color};`,
      selected ? `box-shadow:0 0 0 2px ${color}66;z-index:100;` : 'z-index:1;',
    ].join('');
    el.innerHTML = `<span style="pointer-events:none;padding:4px;overflow:hidden;max-width:90%;">${labels[w.type]||w.type}</span>`;

    // Move: mousedown on the widget body (not on a handle)
    el.addEventListener('mousedown', e => {
      if (e.target.classList.contains('pb-rh')) return;
      e.stopPropagation();
      selectWidget(w.id);
      const rect = canvas.getBoundingClientRect();
      _pbDrag = {
        wId: w.id, mode: 'move',
        startX: e.clientX, startY: e.clientY,
        origX: w.x, origY: w.y, origW: w.w, origH: w.h,
        cw: rect.width, ch: rect.height,
      };
    });

    // Resize handles (only for selected widget)
    if (selected) {
      for (const [handle, css] of HANDLE_DEFS) {
        const h = document.createElement('div');
        h.className = 'pb-rh';
        h.style.cssText = 'position:absolute;width:9px;height:9px;' +
          'background:#fff;border:1.5px solid #555;border-radius:2px;z-index:110;' + css;
        h.addEventListener('mousedown', e => {
          e.stopPropagation();
          e.preventDefault();
          const rect = canvas.getBoundingClientRect();
          _pbDrag = {
            wId: w.id, mode: 'resize', handle,
            startX: e.clientX, startY: e.clientY,
            origX: w.x, origY: w.y, origW: w.w, origH: w.h,
            cw: rect.width, ch: rect.height,
          };
        });
        el.appendChild(h);
      }
    }

    canvas.appendChild(el);
  }
}

// Canvas-level drag events
document.addEventListener('mousemove', e => {
  if (!_pbDrag) return;
  const d = _pbDrag;
  const rawDx = ((e.clientX - d.startX) / d.cw) * 100;
  const rawDy = ((e.clientY - d.startY) / d.ch) * 100;
  const w = _pbWidgets.find(w => w.id === d.wId);
  if (!w) return;

  if (d.mode === 'move') {
    w.x = Math.max(0, Math.min(100 - w.w, snapVal(d.origX + rawDx)));
    w.y = Math.max(0, Math.min(100 - w.h, snapVal(d.origY + rawDy)));
    const el = document.querySelector(`.pb-widget[data-wid="${d.wId}"]`);
    if (el) { el.style.left = w.x + '%'; el.style.top = w.y + '%'; }
    const px = document.getElementById('pb-prop-x'), py = document.getElementById('pb-prop-y');
    if (px) px.value = Math.round(w.x);
    if (py) py.value = Math.round(w.y);

  } else if (d.mode === 'resize') {
    let nx = d.origX, ny = d.origY, nw = d.origW, nh = d.origH;
    const h = d.handle;
    if (h.includes('e')) nw = Math.max(SNAP_GRID, snapVal(d.origW + rawDx));
    if (h.includes('w')) { nw = Math.max(SNAP_GRID, snapVal(d.origW - rawDx)); nx = snapVal(d.origX + rawDx); }
    if (h.includes('s')) nh = Math.max(SNAP_GRID, snapVal(d.origH + rawDy));
    if (h.includes('n')) { nh = Math.max(SNAP_GRID, snapVal(d.origH - rawDy)); ny = snapVal(d.origY + rawDy); }
    // Clamp to canvas bounds
    if (nx < 0) { nw += nx; nx = 0; }
    if (ny < 0) { nh += ny; ny = 0; }
    if (nx + nw > 100) nw = 100 - nx;
    if (ny + nh > 100) nh = 100 - ny;
    nw = Math.max(SNAP_GRID, nw); nh = Math.max(SNAP_GRID, nh);
    w.x = nx; w.y = ny; w.w = nw; w.h = nh;
    const el = document.querySelector(`.pb-widget[data-wid="${d.wId}"]`);
    if (el) { el.style.left = nx+'%'; el.style.top = ny+'%'; el.style.width = nw+'%'; el.style.height = nh+'%'; }
    const px = document.getElementById('pb-prop-x'), py = document.getElementById('pb-prop-y');
    const pw = document.getElementById('pb-prop-w'), ph = document.getElementById('pb-prop-h');
    if (px) px.value = Math.round(nx); if (py) py.value = Math.round(ny);
    if (pw) pw.value = Math.round(nw); if (ph) ph.value = Math.round(nh);
  }
});
document.addEventListener('mouseup', () => { _pbDrag = null; });

/* ── Widget properties panel ─────────────────────── */
function renderWidgetProps() {
  const w = _pbWidgets.find(w => w.id === _pbSelected);
  const panel = document.getElementById('pbProps');
  const title = document.getElementById('pbPropsTitle');
  if (!w) { panel.textContent = 'Sélectionnez un widget.'; title.textContent = 'Propriétés'; return; }

  const labels = {clock:'⏰ Horloge',text:'💬 Texte',weather:'🌤 Météo',media:'🖼 Média',ticker:'📰 Ticker',background:'🎨 Fond'};
  title.textContent = labels[w.type] || w.type;

  const posFields = w.type !== 'background' ? `
    <div class="prop-row"><label>X %</label><input type="number" id="pb-prop-x" value="${Math.round(w.x)}" min="0" max="99" onchange="_pbSet('x',+this.value)"></div>
    <div class="prop-row"><label>Y %</label><input type="number" id="pb-prop-y" value="${Math.round(w.y)}" min="0" max="99" onchange="_pbSet('y',+this.value)"></div>
    <div class="prop-row"><label>Larg %</label><input type="number" id="pb-prop-w" value="${Math.round(w.w)}" min="1" max="100" onchange="_pbSet('w',+this.value)"></div>
    <div class="prop-row"><label>Haut %</label><input type="number" id="pb-prop-h" value="${Math.round(w.h)}" min="1" max="100" onchange="_pbSet('h',+this.value)"></div>
    <hr style="border-color:var(--border);margin:8px 0">` : '';

  let typeFields = '';
  const c = w.config || {};

  if (w.type === 'background') {
    typeFields = `<div class="prop-row"><label>Couleur</label><input type="color" value="${c.color||'#1a1a2e'}" onchange="_pbSetC('color',this.value)"></div>`;
  } else if (w.type === 'clock') {
    typeFields = `
      <div class="prop-row"><label>Taille px</label><input type="number" value="${c.font_size||90}" min="10" max="300" onchange="_pbSetC('font_size',+this.value)"></div>
      <div class="prop-row"><label>Couleur</label><input type="color" value="${c.color||'#ffffff'}" onchange="_pbSetC('color',this.value)"></div>
      <div class="prop-row"><label>Secondes</label><input type="checkbox" ${c.show_seconds?'checked':''} onchange="_pbSetC('show_seconds',this.checked)"></div>
      <div class="prop-row"><label>Afficher date</label><input type="checkbox" ${c.show_date?'checked':''} onchange="_pbSetC('show_date',this.checked)"></div>
      <div class="prop-row"><label>Format date</label><select onchange="_pbSetC('date_format',this.value)"><option value="full" ${(!c.date_format||c.date_format==='full')?'selected':''}>Complet (Lundi 1 avril 2026)</option><option value="short" ${c.date_format==='short'?'selected':''}>Court (JJ/MM/AAAA)</option></select></div>
      <div class="prop-row"><label>Taille date px</label><input type="number" value="${c.date_font_size||28}" min="10" max="200" onchange="_pbSetC('date_font_size',+this.value)"></div>
      <div class="prop-row"><label>Couleur date</label><input type="color" value="${c.date_color||'#aaaacc'}" onchange="_pbSetC('date_color',this.value)"></div>`;
  } else if (w.type === 'text') {
    typeFields = `
      <div class="prop-row"><label>Texte</label><textarea rows="3" style="width:100%;padding:6px;border:1.5px solid var(--border);border-radius:6px;background:var(--surface);color:var(--text);resize:vertical;" onchange="_pbSetC('text',this.value)">${c.text||''}</textarea></div>
      <div class="prop-row"><label>Taille px</label><input type="number" value="${c.font_size||48}" min="8" max="300" onchange="_pbSetC('font_size',+this.value)"></div>
      <div class="prop-row"><label>Couleur</label><input type="color" value="${c.color||'#ffffff'}" onchange="_pbSetC('color',this.value)"></div>
      <div class="prop-row"><label>Alignement</label><select onchange="_pbSetC('align',this.value)"><option value="left" ${c.align==='left'?'selected':''}>Gauche</option><option value="center" ${(!c.align||c.align==='center')?'selected':''}>Centre</option><option value="right" ${c.align==='right'?'selected':''}>Droite</option></select></div>
      <div class="prop-row"><label>Gras</label><input type="checkbox" ${c.bold?'checked':''} onchange="_pbSetC('bold',this.checked)"></div>
      <div class="prop-row"><label>Italique</label><input type="checkbox" ${c.italic?'checked':''} onchange="_pbSetC('italic',this.checked)"></div>`;
  } else if (w.type === 'weather') {
    const hasCoords = c.lat && c.lon;
    typeFields = `
      <div class="prop-row">
        <label>Ville</label>
        <input type="text" id="pb-weather-city" value="${c.city||''}" placeholder="Ex: Paris, Lyon…" style="flex:1" oninput="_pbSetC('city',this.value)">
        <button class="btn btn-ghost btn-sm" onclick="_geocodeWeatherCity()" style="padding:5px 8px;flex-shrink:0">🔍</button>
      </div>
      <div id="pb-weather-coords" style="font-size:11px;color:var(--text-3);padding:2px 0 4px 90px">${hasCoords ? `📍 ${c.lat}, ${c.lon}` : 'Entrez une ville et cliquez 🔍'}</div>
      <div class="prop-row"><label>Unité</label><select onchange="_pbSetC('unit',this.value)"><option value="celsius" ${c.unit!=='fahrenheit'?'selected':''}>Celsius (°C)</option><option value="fahrenheit" ${c.unit==='fahrenheit'?'selected':''}>Fahrenheit (°F)</option></select></div>
      <div class="prop-row"><label>Couleur</label><input type="color" value="${c.color||'#ffffff'}" onchange="_pbSetC('color',this.value)"></div>
      <div style="font-size:11px;color:var(--text-3);padding:2px 0">Tailles et disposition automatiques selon les dimensions du widget.</div>`;
  } else if (w.type === 'media') {
    const srcType = c.source_type || 'all';
    const isFile = srcType.startsWith('file:');
    const selectedFile = isFile ? srcType.slice(5) : '';
    const plOptions = [
      `<option value="all" ${srcType==='all'?'selected':''}>Tous les médias</option>`,
      `<option value="file:" ${isFile?'selected':''}>Fichier unique</option>`,
      ..._builderPlaylists.map(p =>
        `<option value="pl:${p.id}" ${srcType==='pl:'+p.id?'selected':''}>${p.name}</option>`
      )
    ].join('');
    const filePicker = isFile ? `
      <div class="prop-row"><label>Fichier</label><select onchange="_pbSetC('source_type','file:'+this.value)">
        ${_builderFiles.map(f => `<option value="${f.replace(/"/g,'&quot;')}" ${f===selectedFile?'selected':''}>${f}</option>`).join('')}
      </select></div>` : '';
    typeFields = `
      <div class="prop-row"><label>Source</label><select onchange="_pbSetC('source_type',this.value);renderWidgetProps()">${plOptions}</select></div>
      ${filePicker}
      <div class="prop-row"><label>Ajustement</label><select onchange="_pbSetC('fit',this.value)"><option value="contain" ${c.fit!=='cover'?'selected':''}>Contain</option><option value="cover" ${c.fit==='cover'?'selected':''}>Cover</option></select></div>
      ${!isFile ? `<div class="prop-row"><label>Durée img (s)</label><input type="number" value="${c.duration||8}" min="1" max="300" onchange="_pbSetC('duration',+this.value)"></div>` : ''}`;
  } else if (w.type === 'ticker') {
    const isCards = (c.display_mode || 'scroll') === 'cards';
    typeFields = `
      <div class="prop-row"><label>URL RSS</label><input type="url" value="${c.rss_url||''}" placeholder="https://..." onchange="_pbSetC('rss_url',this.value)" style="width:100%"></div>
      <div class="prop-row"><label>Affichage</label><select onchange="_pbSetC('display_mode',this.value);renderWidgetProps()"><option value="scroll" ${!isCards?'selected':''}>Défilant (ticker)</option><option value="cards" ${isCards?'selected':''}>Cartes avec images</option></select></div>
      <div class="prop-row"><label>Taille px</label><input type="number" value="${c.font_size||28}" min="10" max="100" onchange="_pbSetC('font_size',+this.value)"></div>
      <div class="prop-row"><label>Couleur texte</label><input type="color" value="${c.color||'#ffffff'}" onchange="_pbSetC('color',this.value)"></div>
      <div class="prop-row"><label>Couleur fond</label><input type="color" value="${_rgbaToHex(c.bg_color||'#000000')}" onchange="_pbSetC('bg_color',this.value)"></div>
      ${isCards
        ? `<div class="prop-row"><label>Durée/carte (s)</label><input type="number" value="${c.card_duration||8}" min="2" max="60" onchange="_pbSetC('card_duration',+this.value)"></div>`
        : `<div class="prop-row"><label>Vitesse px/s</label><input type="number" value="${c.speed||60}" min="10" max="500" onchange="_pbSetC('speed',+this.value)"></div>`
      }`;
  }

  panel.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:6px;">
      ${posFields}${typeFields}
      <button class="btn btn-ghost btn-sm" style="color:var(--red);margin-top:8px;" onclick="deleteSelectedWidget()">Supprimer ce widget</button>
    </div>`;
}

function _pbSet(key, val) {
  const w = _pbWidgets.find(w => w.id === _pbSelected);
  if (!w) return;
  w[key] = val;
  renderCanvas();
}
function _pbSetC(key, val) {
  const w = _pbWidgets.find(w => w.id === _pbSelected);
  if (!w) return;
  if (!w.config) w.config = {};
  w.config[key] = val;
}
function _rgbaToHex(c) {
  if (!c || c.startsWith('#')) return c || '#000000';
  return '#000000'; // fallback for rgba strings
}

async function _geocodeWeatherCity() {
  const input = document.getElementById('pb-weather-city');
  const coordsEl = document.getElementById('pb-weather-coords');
  if (!input || !coordsEl) return;
  const city = input.value.trim();
  if (!city) return;
  _pbSetC('city', city);
  coordsEl.textContent = '⏳ Recherche en cours…';
  try {
    const resp = await fetch(
      'https://geocoding-api.open-meteo.com/v1/search?name=' +
      encodeURIComponent(city) + '&count=1&language=fr&format=json'
    );
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    if (!data.results || !data.results.length) {
      coordsEl.textContent = '❌ Ville introuvable';
      return;
    }
    const r = data.results[0];
    _pbSetC('lat', r.latitude);
    _pbSetC('lon', r.longitude);
    _pbSetC('city', r.name);
    input.value = r.name;
    coordsEl.textContent = `📍 ${r.latitude}, ${r.longitude}${r.country ? ' — ' + r.country : ''}`;
  } catch(e) {
    coordsEl.textContent = '❌ Erreur : ' + e.message;
  }
}

// ── Init ──────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const hash = window.location.hash.replace('#','');
  if (['dashboard','medias','playlists','settings','pages'].includes(hash)) goTo(hash);

  if (window.location.search.includes('error=files')) {
    showToast('Nombre maximum de fichiers atteint -- augmentez votre licence');
    history.replaceState(null, '', '/');
  } else if (window.location.search.includes('error=quota')) {
    showToast('Espace de stockage insuffisant -- augmentez votre licence');
    history.replaceState(null, '', '/');
  }

  loadConfig();
  loadGallery();
  loadDashboard();
  loadLicense();
  loadGitStatus();

  setInterval(() => {
    if (!isDragging) {
      loadDashboard();
      if (_currentPage === 'medias') loadGallery();
    }
  }, 8000);
});
