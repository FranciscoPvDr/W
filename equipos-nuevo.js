const SERVER = window.location.origin;
let TOKEN = localStorage.getItem('monitor_token');
let step = 1;
let selectedTipo = 'Laptop';
let empleados = [];
let assets = [];
let empleadoSeleccionado = null;
let editingAsset = null;

if (!TOKEN) location.href = '/login';

const TIPOS = [
  { tipo: 'Laptop', icon: '💻', desc: 'Equipo portátil con cargador asociado.' },
  { tipo: 'Desktop', icon: '🖥️', desc: 'Equipo fijo con mouse, teclado y monitor.' },
  { tipo: 'Monitor', icon: '🖥', desc: 'Pantalla independiente asignable.' },
  { tipo: 'UPS', icon: '🔋', desc: 'Respaldo eléctrico y protección.' },
  { tipo: 'Red', icon: '🌐', desc: 'Switch, router, firewall o equipo de red.' },
];

const PREFIX = { Laptop: 'LAP', Desktop: 'DESK', Monitor: 'MON', UPS: 'UPS', Red: 'RED' };

function headers() { return { Authorization: `Bearer ${TOKEN}` }; }
function jsonHeaders() { return { ...headers(), 'Content-Type': 'application/json' }; }
function logout() { localStorage.clear(); location.href = '/login'; }
function esc(v) { return String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }
function norm(v) { return String(v || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/\s+/g, ' ').trim().toUpperCase(); }

function setMsg(text) { msg.textContent = text || ''; }

function renderProgress() {
  const labels = ['Tipo', 'Datos generales', 'Complementos', 'Asignación'];
  progress.innerHTML = labels.map((label, i) => {
    const n = i + 1;
    const cls = n === step ? 'active' : n < step ? 'done' : '';
    return `<div class="progress-step ${cls}"><span class="step-chip">Paso ${n}</span><div>${label}</div></div>`;
  }).join('');
}

function renderTipos() {
  typeGrid.innerHTML = TIPOS.map((t) => `<div class="type-card ${t.tipo === selectedTipo ? 'selected' : ''}" onclick="seleccionarTipo('${t.tipo}')">
    <div class="type-icon">${t.icon}</div>
    <div class="type-name">${t.tipo}</div>
    <div class="type-desc">${t.desc}</div>
  </div>`).join('');
}

function seleccionarTipo(tipo) {
  selectedTipo = tipo;
  renderTipos();
  sugerirInventario();
  renderComplementos();
}

function setValue(id, value) {
  const el = document.getElementById(id);
  if (el) el.value = value || '';
}

function prefijoTipo() { return PREFIX[selectedTipo] || selectedTipo.toUpperCase().slice(0, 3); }

function sugerirInventario() {
  if (editingAsset) return;
  if (numInventario.value.trim()) return;
  const pref = `MC-${prefijoTipo()}-`;
  const nums = assets.map((a) => String(a.numInventario || '').toUpperCase())
    .filter((n) => n.startsWith(pref))
    .map((n) => parseInt((n.match(/(\d+)$/) || [])[1] || '0', 10))
    .filter(Boolean);
  const next = Math.max(90, ...nums) + 1;
  numInventario.value = `${pref}${String(next).padStart(3, '0')}`;
  inventarioHint.textContent = `Sugerido automáticamente para ${selectedTipo}. Puedes modificarlo antes de guardar.`;
}

function renderComplementos() {
  if (selectedTipo === 'Laptop') {
    complementosStep.innerHTML = `<div class="section-divider"><div class="section-title">🔌 Cargador</div><div class="grid-2">
      <div><label>No. inventario cargador</label><input id="cargador_id" /></div>
      <div><label>Marca</label><input id="cargador_marca" /></div>
      <div><label>Modelo</label><input id="cargador_modelo" /></div>
      <div><label>Serie</label><input id="cargador_serie" /></div>
    </div></div>`;
    if (editingAsset) {
      setValue('cargador_id', editingAsset.cargador_id);
      setValue('cargador_marca', editingAsset.cargador_marca);
      setValue('cargador_modelo', editingAsset.cargador_modelo);
      setValue('cargador_serie', editingAsset.cargador_serie);
    }
    return;
  }
  if (selectedTipo === 'Desktop') {
    complementosStep.innerHTML = `<div class="section-divider"><div class="section-title">🖱️ Mouse</div><div class="grid-2">
      <div><label>No. inventario</label><input id="mouse_numInventario" /></div>
      <div><label>Marca</label><input id="mouse_marca" /></div>
      <div><label>Modelo</label><input id="mouse_modelo" /></div>
      <div><label>Serie</label><input id="mouse_serie" /></div>
    </div></div><div class="section-divider"><div class="section-title">⌨️ Teclado</div><div class="grid-2">
      <div><label>No. inventario</label><input id="teclado_numInventario" /></div>
      <div><label>Marca</label><input id="teclado_marca" /></div>
      <div><label>Modelo</label><input id="teclado_modelo" /></div>
      <div><label>Serie</label><input id="teclado_serie" /></div>
    </div></div>`;
    if (editingAsset) {
      const mouse = parseComplemento(editingAsset.mouse);
      const teclado = parseComplemento(editingAsset.teclado);
      setValue('mouse_numInventario', mouse.numInventario || mouse.num_inventario || mouse.id);
      setValue('mouse_marca', mouse.marca);
      setValue('mouse_modelo', mouse.modelo);
      setValue('mouse_serie', mouse.serie);
      setValue('teclado_numInventario', teclado.numInventario || teclado.num_inventario || teclado.id);
      setValue('teclado_marca', teclado.marca);
      setValue('teclado_modelo', teclado.modelo);
      setValue('teclado_serie', teclado.serie);
    }
    return;
  }
  complementosStep.innerHTML = '<div class="empty-box">Este tipo no tiene complementos. Puedes continuar a asignación.</div>';
}

function parseComplemento(value) {
  if (!value) return {};
  if (typeof value === 'object') return value;
  try {
    return JSON.parse(value) || {};
  } catch {
    return {};
  }
}

function mostrarStep() {
  [1, 2, 3, 4].forEach((n) => document.getElementById(`step${n}`).classList.toggle('active', n === step));
  btnPrev.disabled = step === 1;
  btnNext.style.display = step === 4 ? 'none' : '';
  btnSave.style.display = step === 4 ? '' : 'none';
  renderProgress();
  setMsg('');
  if (step === 2) sugerirInventario();
  if (step === 3) renderComplementos();
  if (step === 3 && !['Laptop', 'Desktop'].includes(selectedTipo)) {
    setTimeout(() => {
      if (step === 3 && !['Laptop', 'Desktop'].includes(selectedTipo)) {
        step = 4;
        mostrarStep();
      }
    }, 900);
  }
}

function validarPaso() {
  if (step === 1 && !selectedTipo) return 'Selecciona un tipo de asset.';
  if (step === 2) {
    if (!numInventario.value.trim()) return 'Captura el número de inventario.';
    if (!marca.value.trim()) return 'Captura la marca.';
    if (!modelo.value.trim()) return 'Captura el modelo.';
    if (!serie.value.trim()) return 'Captura la serie.';
  }
  return '';
}

function siguiente() {
  const err = validarPaso();
  if (err) { setMsg(err); return; }
  if (step < 4) step += 1;
  mostrarStep();
}

function anterior() {
  if (step > 1) step -= 1;
  mostrarStep();
}

function jsonComplemento(prefix) {
  const data = {
    numInventario: document.getElementById(`${prefix}_numInventario`)?.value.trim() || '',
    marca: document.getElementById(`${prefix}_marca`)?.value.trim() || '',
    modelo: document.getElementById(`${prefix}_modelo`)?.value.trim() || '',
    serie: document.getElementById(`${prefix}_serie`)?.value.trim() || '',
  };
  return Object.values(data).some(Boolean) ? data : null;
}

function payload() {
  const p = {
    tipo: selectedTipo,
    numInventario: numInventario.value.trim(),
    marca: marca.value.trim(),
    modelo: modelo.value.trim(),
    serie: serie.value.trim(),
    estado: estado.value,
    fechaCompra: fechaCompra.value.trim(),
    notas: notas.value.trim(),
    asignado: asignado.value.trim(),
    departamento: departamento.value.trim(),
    puesto: puesto.value.trim(),
  };
  if (selectedTipo === 'Laptop') {
    p.cargador_id = document.getElementById('cargador_id')?.value.trim() || '';
    p.cargador_marca = document.getElementById('cargador_marca')?.value.trim() || '';
    p.cargador_modelo = document.getElementById('cargador_modelo')?.value.trim() || '';
    p.cargador_serie = document.getElementById('cargador_serie')?.value.trim() || '';
  }
  if (selectedTipo === 'Desktop') {
    p.mouse = jsonComplemento('mouse');
    p.teclado = jsonComplemento('teclado');
  }
  return p;
}

function optionEmpleado(e) {
  return `<div class="employee-option" onclick="seleccionarEmpleado('${esc(e.nombreCompleto)}')"><strong>${esc(e.nombreCompleto)}</strong><div class="muted">${esc(e.departamento || '')} ${e.puesto ? `· ${esc(e.puesto)}` : ''}</div></div>`;
}

function filtrarEmpleados() {
  const term = norm(empleadoSearch.value);
  const rows = empleados.filter((e) => !term || norm(`${e.nombreCompleto} ${e.departamento} ${e.puesto}`).includes(term)).slice(0, 12);
  employeeResults.innerHTML = rows.map(optionEmpleado).join('') || '<div class="employee-option muted">Sin resultados</div>';
  employeeResults.style.display = 'block';
}

function seleccionarEmpleado(nombre) {
  const e = empleados.find((x) => norm(x.nombreCompleto) === norm(nombre));
  empleadoSeleccionado = e || null;
  asignado.value = e ? e.nombreCompleto : '';
  departamento.value = e ? e.departamento || '' : '';
  puesto.value = e ? e.puesto || '' : '';
  empleadoSearch.value = e ? e.nombreCompleto : '';
  employeeResults.style.display = 'none';
}

function sinAsignar() {
  empleadoSeleccionado = null;
  empleadoSearch.value = '';
  asignado.value = '';
  departamento.value = '';
  puesto.value = '';
  employeeResults.style.display = 'none';
}

async function guardar() {
  const err = validarPaso();
  if (err) { setMsg(err); return; }
  btnSave.disabled = true;
  setMsg('Guardando asset...');
  const body = payload();
  const url = editingAsset ? `${SERVER}/api/assets/${encodeURIComponent(editingAsset.id)}` : `${SERVER}/api/assets`;
  const res = await fetch(url, { method: editingAsset ? 'PATCH' : 'POST', headers: jsonHeaders(), body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    btnSave.disabled = false;
    setMsg(data.detail || 'No se pudo guardar el asset.');
    return;
  }
  location.href = `/equipos/${encodeURIComponent(body.numInventario)}`;
}

async function cargarEdicion() {
  const edit = new URLSearchParams(location.search).get('edit');
  if (!edit) return;
  const res = await fetch(`${SERVER}/api/assets/by-inventario/${encodeURIComponent(edit)}`, { headers: headers() });
  if (!res.ok) return;
  const data = await res.json();
  editingAsset = data.asset;
  selectedTipo = editingAsset.tipo || 'Laptop';
  renderTipos();
  setValue('numInventario', editingAsset.numInventario);
  setValue('marca', editingAsset.marca);
  setValue('modelo', editingAsset.modelo);
  setValue('serie', editingAsset.serie);
  setValue('estado', editingAsset.estado || 'Activo');
  setValue('fechaCompra', editingAsset.fechaCompra);
  setValue('notas', editingAsset.notas);
  setValue('asignado', editingAsset.asignado);
  setValue('departamento', editingAsset.departamento);
  setValue('puesto', editingAsset.puesto);
  setValue('empleadoSearch', editingAsset.asignado);
  renderComplementos();
}

async function cargarDatos() {
  const [resAssets, resEmps] = await Promise.all([
    fetch(`${SERVER}/api/assets`, { headers: headers() }),
    fetch(`${SERVER}/api/empleados`, { headers: headers() }),
  ]);
  if (resAssets.status === 401 || resEmps.status === 401) { logout(); return; }
  const assetData = resAssets.ok ? await resAssets.json() : { assets: [] };
  const empData = resEmps.ok ? await resEmps.json() : { empleados: [] };
  assets = assetData.assets || [];
  empleados = (empData.empleados || []).filter((e) => e.activo !== false);
  await cargarEdicion();
  sugerirInventario();
}

empleadoSearch.addEventListener('input', filtrarEmpleados);
empleadoSearch.addEventListener('focus', filtrarEmpleados);
document.addEventListener('click', (e) => {
  if (!e.target.closest('.search-wrap')) employeeResults.style.display = 'none';
});

renderTipos();
renderComplementos();
mostrarStep();
cargarDatos();
