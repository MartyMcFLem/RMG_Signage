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
    const [status, storage, lic] = await Promise.all([
      fetch('/api/status').then(r => r.json()),
      fetch('/api/storage').then(r => r.json()),
      fetch('/api/license').then(r => r.json()),
    ]);

    const mpvEl = document.getElementById('dashMpvStatus');
    const mpvSub = document.getElementById('dashMpvSub');
    if (status.mpv_running) {
      mpvEl.innerHTML = '<span class="status-dot on"></span>Actif';
      mpvSub.textContent = status.media_count + ' medias charges';
    } else {
      mpvEl.innerHTML = '<span class="status-dot off"></span>Arrete';
      mpvSub.textContent = '';
    }

    document.getElementById('dashMediaCount').textContent = status.media_count;
    document.getElementById('dashMediaSub').textContent = status.media_count > 1 ? 'fichiers' : 'fichier';

    const tier = lic.tier || 'none';
    document.getElementById('dashLicense').textContent = tier.charAt(0).toUpperCase() + tier.slice(1);
    const qMb = lic.media_quota_mb || 0;
    document.getElementById('dashLicenseSub').textContent = qMb >= 1024 ? (qMb/1024).toFixed(0) + ' Go alloues' : qMb + ' Mo alloues';

    if (status.serial) {
      document.getElementById('navSerial').textContent = status.serial.replace('rmg-sign-','').substring(0,8) + '...';
      document.getElementById('navSerialFull').textContent = status.serial;
    }

    const used = storage.used_mb || 0;
    const total = storage.total_mb || 1;
    const pct = storage.usage_percent || 0;
    const fmtSize = mb => mb >= 1024 ? (mb/1024).toFixed(1)+' Go' : mb+' Mo';
    document.getElementById('storageUsed').textContent = fmtSize(used) + ' utilises';
    document.getElementById('storageTotal').textContent = fmtSize(total);
    document.getElementById('storagePct').textContent = pct + '% utilise';
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
    if (status.mpv_running) {
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
  await fetch('/api/play-single/'+encodeURIComponent(filename), {method:'POST'});
  loadGallery(); loadConfig();
  showToast('Affichage de '+filename);
}
async function playAll() {
  await fetch('/api/play-all', {method:'POST'});
  loadGallery(); loadConfig();
  showToast('Lecture de toute la mediatheque');
}
async function deleteFile(filename) {
  if (!confirm('Supprimer '+filename+' ?')) return;
  await fetch('/api/delete/'+encodeURIComponent(filename), {method:'DELETE'});
  loadGallery(); loadDashboard();
  showToast(filename+' supprime');
}
async function control(action) {
  const res = await fetch('/api/control/'+action, {method:'POST'});
  const data = await res.json();
  showToast(data.message);
  loadConfig(); loadDashboard();
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
    list.innerHTML = pls.map(pl => {
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
              : '<button class="btn btn-blue btn-sm" onclick="activatePlaylist(\''+pl.id+'\')">Lire</button>'}
            <button class="btn btn-ghost btn-sm" onclick="openPlModal(\''+pl.id+'\')">Editer</button>
            <button class="btn btn-ghost btn-sm" style="color:var(--red)" onclick="deletePlaylist(\''+pl.id+'\',\''+pl.name.replace(/'/g,"\\'")+'\')">Suppr.</button>
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
  const res = await fetch('/api/playlists/' + id + '/activate', {method: 'POST'});
  const data = await res.json();
  showToast(data.message);
  loadPlaylists(); loadDashboard(); loadConfig();
}

async function deactivatePlaylist() {
  await fetch('/api/playlists/deactivate', {method: 'POST'});
  showToast('Lecture de tous les fichiers');
  loadPlaylists(); loadDashboard(); loadConfig();
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

// ── Init ──────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const hash = window.location.hash.replace('#','');
  if (['dashboard','medias','playlists','settings'].includes(hash)) goTo(hash);

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
