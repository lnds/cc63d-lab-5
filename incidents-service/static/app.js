// El frontend corre en el navegador, fuera del clúster. Solo puede hablar con lo
// que está expuesto: incidents-service (NodePort). El catálogo (ClusterIP, interno)
// no es accesible directo; por eso el selector de servicios va contra el proxy
// /catalog/services, que incidents-service reenvía al catálogo dentro del clúster.
const API = '';

async function api(path, opts = {}) {
  const res = await fetch(`${API}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  return res.json();
}

// --- Navegación ---

let currentFilter = '';

document.querySelectorAll('.nav-btn').forEach(btn => {
  btn.addEventListener('click', () => showSection(btn.dataset.section));
});

document.querySelectorAll('.filter-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentFilter = btn.dataset.status;
    loadIncidents();
  });
});

function showSection(name) {
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  const navBtn = document.querySelector(`.nav-btn[data-section="${name}"]`);
  if (navBtn) navBtn.classList.add('active');

  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.getElementById(name).classList.add('active');

  if (name === 'incidents') loadIncidents();
  if (name === 'postmortems') loadPostmortems();
}

// --- Modales ---

function showModal(id) {
  if (id === 'new-incident-modal') loadServiceOptions();
  document.getElementById(id).classList.add('active');
}

function hideModals() {
  document.querySelectorAll('.modal').forEach(m => m.classList.remove('active'));
}

document.querySelectorAll('.modal').forEach(modal => {
  modal.addEventListener('click', e => { if (e.target === modal) hideModals(); });
});

// --- Servicios (vía el proxy al catálogo) ---

async function loadServiceOptions() {
  const services = await api('/catalog/services');
  const html = services.map(s => `<option value="${s.id}">${s.name}</option>`).join('');
  document.getElementById('incident-service-select').innerHTML = html;
}

// --- Incidentes ---

async function loadIncidents() {
  const url = currentFilter ? `/incidents?status=${currentFilter}` : '/incidents';
  const incidents = await api(url);
  const el = document.getElementById('incidents-list');
  if (!incidents.length) {
    el.innerHTML = '<div class="empty-state">No hay incidentes.</div>';
    return;
  }
  el.innerHTML = incidents.map(i => `
    <div class="card" onclick="showIncident(${i.id})">
      <div class="card-top">
        <div>
          <div class="card-title">${i.title}</div>
          <div class="card-meta">
            <span>${i.service_name}</span>
            <span>${formatTime(i.started_at)}</span>
            ${i.resolved_at ? `<span>Duración: ${duration(i.started_at, i.resolved_at)}</span>` : ''}
          </div>
        </div>
        <div style="display: flex; gap: 0.5rem">
          <span class="badge badge-sev${i.severity}">SEV${i.severity}</span>
          <span class="badge badge-${i.status}">${i.status}</span>
        </div>
      </div>
    </div>
  `).join('');
}

async function createIncident(e) {
  e.preventDefault();
  const form = e.target;
  const data = Object.fromEntries(new FormData(form));
  data.service_id = parseInt(data.service_id);
  data.severity = parseInt(data.severity);
  const result = await api('/incidents', { method: 'POST', body: data });
  form.reset();
  hideModals();
  if (result.id) {
    showIncident(result.id);
  } else {
    loadIncidents();
  }
}

async function showIncident(id) {
  const incident = await api(`/incidents/${id}`);

  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.getElementById('incident-detail').classList.add('active');

  const hasPostmortem = incident.timeline?.some(t => t.message === 'Post-mortem published');

  document.getElementById('incident-detail-content').innerHTML = `
    <div class="detail-header">
      <div class="detail-badges">
        <span class="badge badge-sev${incident.severity}">SEV${incident.severity}</span>
        <span class="badge badge-${incident.status}">${incident.status}</span>
      </div>
      <h2>${incident.title}</h2>
      <div class="detail-meta">
        <div>Servicio: <strong>${incident.service_name}</strong></div>
        <div>Inicio: ${formatTime(incident.started_at)}</div>
        ${incident.resolved_at ? `<div>Resuelto: ${formatTime(incident.resolved_at)} (${duration(incident.started_at, incident.resolved_at)})</div>` : ''}
        <div>Creado por: ${incident.created_by}</div>
      </div>
      <div class="detail-actions">
        ${incident.status !== 'resolved' ? `<button class="btn btn-primary btn-sm" onclick="showUpdateModal(${incident.id})">Actualizar</button>` : ''}
        ${incident.status === 'resolved' && !hasPostmortem ? `<button class="btn btn-primary btn-sm" onclick="showPostmortemModal(${incident.id})">Escribir post-mortem</button>` : ''}
      </div>
    </div>
    <div class="timeline">
      <h3>Línea de tiempo</h3>
      ${incident.timeline.map(t => `
        <div class="timeline-item">
          <div class="timeline-time">${formatTime(t.timestamp)}</div>
          <div class="timeline-author">${t.author}</div>
          <div class="timeline-message">${t.message}</div>
        </div>
      `).join('')}
    </div>
  `;
}

function showUpdateModal(incidentId) {
  document.getElementById('update-incident-form').reset();
  document.getElementById('update-incident-id').value = incidentId;
  showModal('update-incident-modal');
}

async function updateIncident(e) {
  e.preventDefault();
  const form = e.target;
  const data = Object.fromEntries(new FormData(form));
  const id = data.incident_id;
  delete data.incident_id;
  if (!data.status) delete data.status;
  if (!data.message) delete data.message;
  await api(`/incidents/${id}`, { method: 'PATCH', body: data });
  hideModals();
  showIncident(id);
}

function showPostmortemModal(incidentId) {
  document.getElementById('new-postmortem-form').reset();
  document.getElementById('pm-incident-id').value = incidentId;
  showModal('new-postmortem-modal');
}

// --- Post-Mortems ---

async function loadPostmortems() {
  const pms = await api('/postmortems');
  const el = document.getElementById('postmortems-list');
  if (!pms.length) {
    el.innerHTML = '<div class="empty-state">No hay post-mortems. Resuelve un incidente primero.</div>';
    return;
  }
  el.innerHTML = pms.map(pm => `
    <div class="card" onclick="showPostmortem(${pm.id})">
      <div class="card-top">
        <div>
          <div class="card-title">${pm.incident_title}</div>
          <div class="card-meta">
            <span>${pm.service_name}</span>
            <span>SEV${pm.severity}</span>
            <span>${formatTime(pm.created_at)}</span>
          </div>
        </div>
      </div>
      <p style="margin-top: 0.5rem; font-size: 0.85rem; color: var(--text-dim)">${pm.summary.substring(0, 150)}...</p>
    </div>
  `).join('');
}

async function showPostmortem(id) {
  const pm = await api(`/postmortems/${id}`);
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.getElementById('incident-detail').classList.add('active');

  document.getElementById('incident-detail-content').innerHTML = `
    <div class="detail-header">
      <div class="detail-badges">
        <span class="badge badge-sev${pm.severity}">SEV${pm.severity}</span>
        <span class="badge badge-resolved">POST-MORTEM</span>
      </div>
      <h2>${pm.incident_title}</h2>
      <div class="detail-meta">
        <div>Servicio: <strong>${pm.service_name}</strong></div>
        <div>Publicado: ${formatTime(pm.created_at)}</div>
      </div>
    </div>
    <div class="pm-section"><h4>Resumen</h4><p>${pm.summary}</p></div>
    <div class="pm-section"><h4>Causa raíz</h4><p>${pm.root_cause}</p></div>
    <div class="pm-section"><h4>Impacto</h4><p>${pm.impact}</p></div>
    <div class="pm-section"><h4>Acciones a tomar</h4><p>${pm.action_items}</p></div>
    ${pm.lessons ? `<div class="pm-section"><h4>Lecciones aprendidas</h4><p>${pm.lessons}</p></div>` : ''}
  `;
}

async function createPostmortem(e) {
  e.preventDefault();
  const form = e.target;
  const data = Object.fromEntries(new FormData(form));
  data.incident_id = parseInt(data.incident_id);
  await api('/postmortems', { method: 'POST', body: data });
  form.reset();
  hideModals();
  showSection('postmortems');
}

// --- Helpers ---

function formatTime(iso) {
  if (!iso) return '';
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z');
  return d.toLocaleString();
}

function duration(start, end) {
  const ms = new Date(end.endsWith('Z') ? end : end + 'Z') - new Date(start.endsWith('Z') ? start : start + 'Z');
  const mins = Math.floor(ms / 60000);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  return `${hours}h ${mins % 60}m`;
}

// --- Init ---
loadIncidents();
