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
  { tipo: 'Red', icon: '🌐', desc: 'Access Point, switch, firewall o balanceador.' },
  { tipo: 'Impresora', icon: '🖨️', desc: 'Láser, tinta o térmica para tickets.' },
  { tipo: 'Plotter', icon: '📐', desc: 'Impresión gran formato.' },
];

const PREFIX = { Laptop: 'LAP', Desktop: 'DESK', Monitor: 'MON', UPS: 'UPS', Red: 'RED', Impresora: 'IMP', Plotter: 'PLT' };
const NUEVO_VALOR = '__nuevo__';
const NOTAS_MARKER = '--- Características técnicas ---';
const SUBTIPOS = {
  Red: ['Access Point', 'Switch', 'Firewall', 'Balanceador'],
  Impresora: ['Láser/Tinta', 'Térmica (tickets)'],
};
const CARACTERISTICAS = {
  Laptop: [{ id: 'cpu', label: 'Procesador' }, { id: 'ram', label: 'RAM' }, { id: 'almacenamiento', label: 'Almacenamiento' }, { id: 'pantalla', label: 'Pantalla' }],
  Desktop: [{ id: 'cpu', label: 'Procesador' }, { id: 'ram', label: 'RAM' }, { id: 'almacenamiento', label: 'Almacenamiento' }, { id: 'formato', label: 'Formato gabinete' }],
  Monitor: [{ id: 'tamano', label: 'Tamaño' }, { id: 'resolucion', label: 'Resolución' }, { id: 'entradas', label: 'Entradas' }],
  UPS: [{ id: 'capacidad', label: 'Capacidad VA/W' }, { id: 'contactos', label: 'Contactos' }, { id: 'bateria', label: 'Batería' }],
  Plotter: [{ id: 'ancho', label: 'Ancho máximo' }, { id: 'tecnologia', label: 'Tecnología' }, { id: 'conectividad', label: 'Conectividad' }],
  'Red:Access Point': [{ id: 'frecuencia', label: 'Frecuencia' }, { id: 'wifi', label: 'Estándar WiFi' }, { id: 'poe', label: 'PoE' }],
  'Red:Switch': [{ id: 'puertos', label: 'Puertos' }, { id: 'velocidad', label: 'Velocidad' }, { id: 'poe', label: 'PoE' }, { id: 'administrable', label: 'Administrable' }],
  'Red:Firewall': [{ id: 'throughput', label: 'Throughput' }, { id: 'vpn', label: 'VPN' }, { id: 'puertos', label: 'Puertos' }],
  'Red:Balanceador': [{ id: 'throughput', label: 'Throughput' }, { id: 'puertos', label: 'Puertos' }, { id: 'servicios', label: 'Servicios' }],
  'Impresora:Láser/Tinta': [{ id: 'color', label: 'Color/Monocromo' }, { id: 'duplex', label: 'Dúplex' }, { id: 'conectividad', label: 'Conectividad' }],
  'Impresora:Térmica (tickets)': [{ id: 'anchoPapel', label: 'Ancho de papel' }, { id: 'corte', label: 'Corte automático' }, { id: 'conectividad', label: 'Conectividad' }],
};

function headers() { return { Authorization: `Bearer ${TOKEN}` }; }
function jsonHeaders() { return { ...headers(), 'Content-Type': 'application/json' }; }
function logout() { localStorage.clear(); location.href = '/login'; }
function esc(v) { return String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }
function norm(v) { return String(v || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/\s+/g, ' ').trim().toUpperCase(); }

function setMsg(text) { msg.textContent = text || ''; }

function setWizardMode() {
  const title = document.getElementById('wizardTitle');
  const subtitle = document.getElementById('wizardSubtitle');
  const logo = document.getElementById('wizardLogo');
  if (!title || !subtitle) return;
  if (editingAsset) {
    if (logo) logo.textContent = 'Monitor · Editar asset';
    title.textContent = 'Editar asset';
    subtitle.textContent = `Modificando ${editingAsset.numInventario || editingAsset.id || 'asset'} desde el wizard guiado.`;
    btnSave.textContent = 'Guardar cambios';
  } else {
    if (logo) logo.textContent = 'Monitor · Nuevo asset';
    title.textContent = 'Alta de asset';
    subtitle.textContent = 'Captura inventario, complementos y asignación en 4 pasos guiados.';
    btnSave.textContent = 'Guardar';
  }
}

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
  renderSubtipos();
  renderMarcaModelo();
  sugerirInventario();
  renderComplementos();
}

function setValue(id, value) {
  const el = document.getElementById(id);
  if (el) el.value = value || '';
}

function caracteristicaKey() {
  const st = subtipo?.value || '';
  return st ? `${selectedTipo}:${st}` : selectedTipo;
}

function camposCaracteristicas() {
  return CARACTERISTICAS[caracteristicaKey()] || CARACTERISTICAS[selectedTipo] || [];
}

function parseNotas(notasRaw) {
  const notasText = String(notasRaw || '');
  const [base, bloque] = notasText.split(NOTAS_MARKER);
  const datos = {};
  if (bloque) {
    bloque.split('\n').forEach((line) => {
      const idx = line.indexOf(':');
      if (idx > -1) datos[norm(line.slice(0, idx))] = line.slice(idx + 1).trim();
    });
  }
  return { base: base.trim(), datos };
}

function notasConCaracteristicas() {
  const base = notas.value.trim();
  const rows = camposCaracteristicas().map((c) => {
    const value = document.getElementById(`car_${c.id}`)?.value.trim() || '';
    return value ? `${c.label}: ${value}` : '';
  }).filter(Boolean);
  return [base, rows.length ? `${NOTAS_MARKER}\n${rows.join('\n')}` : ''].filter(Boolean).join('\n\n');
}

function renderSubtipos(value = '') {
  const wrap = document.getElementById('subtipoWrap');
  if (!wrap || !subtipo) return;
  const opts = SUBTIPOS[selectedTipo] || [];
  wrap.style.display = opts.length ? '' : 'none';
  subtipo.innerHTML = opts.map((o) => `<option value="${esc(o)}">${esc(o)}</option>`).join('');
  if (opts.length) subtipo.value = opts.includes(value) ? value : opts[0];
  else subtipo.innerHTML = '';
}

function valoresUnicos(rows, field) {
  const seen = new Set();
  return rows.map((a) => a[field]).filter(Boolean).filter((v) => {
    const k = norm(v);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  }).sort((a, b) => a.localeCompare(b));
}

function valorMarca() {
  return marcaSelect.value === NUEVO_VALOR ? marcaNueva.value.trim() : marcaSelect.value;
}

function valorModelo() {
  return modeloSelect.value === NUEVO_VALOR ? modeloNuevo.value.trim() : modeloSelect.value;
}

function renderMarcaModelo(marcaActual = valorMarca(), modeloActual = valorModelo()) {
  if (!marcaSelect || !modeloSelect) return;
  const rowsTipo = assets.filter((a) => norm(a.tipo) === norm(selectedTipo));
  const marcas = valoresUnicos(rowsTipo, 'marca');
  marcaSelect.innerHTML = '<option value="">Selecciona marca</option>' + marcas.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join('') + `<option value="${NUEVO_VALOR}">+ Agregar nueva marca</option>`;
  marcaSelect.value = marcas.some((m) => norm(m) === norm(marcaActual)) ? marcas.find((m) => norm(m) === norm(marcaActual)) : marcaActual ? NUEVO_VALOR : '';
  marcaNueva.style.display = marcaSelect.value === NUEVO_VALOR ? '' : 'none';
  marcaNueva.value = marcaSelect.value === NUEVO_VALOR ? marcaActual : '';
  renderModelos(valorMarca(), modeloActual);
}

function renderModelos(marcaActual = valorMarca(), modeloActual = valorModelo()) {
  if (!modeloSelect) return;
  const rowsMarca = assets.filter((a) => norm(a.tipo) === norm(selectedTipo) && norm(a.marca) === norm(marcaActual));
  const modelos = valoresUnicos(rowsMarca, 'modelo');
  modeloSelect.innerHTML = '<option value="">Selecciona modelo</option>' + modelos.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join('') + `<option value="${NUEVO_VALOR}">+ Agregar nuevo modelo</option>`;
  modeloSelect.value = modelos.some((m) => norm(m) === norm(modeloActual)) ? modelos.find((m) => norm(m) === norm(modeloActual)) : modeloActual ? NUEVO_VALOR : '';
  modeloNuevo.style.display = modeloSelect.value === NUEVO_VALOR ? '' : 'none';
  modeloNuevo.value = modeloSelect.value === NUEVO_VALOR ? modeloActual : '';
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
  const notasParsed = parseNotas(editingAsset?.notas || notas.value);
  const caracteristicas = camposCaracteristicas();
  const caracteristicasHtml = caracteristicas.length ? `<div class="section-divider"><div class="section-title">Características de ${esc(selectedTipo)}${subtipo?.value ? ` · ${esc(subtipo.value)}` : ''}</div><div class="grid-2">
    ${caracteristicas.map((c) => `<div><label>${esc(c.label)}</label><input id="car_${esc(c.id)}" value="${esc(notasParsed.datos[norm(c.label)] || '')}" /></div>`).join('')}
  </div></div>` : '';
  if (selectedTipo === 'Laptop') {
    complementosStep.innerHTML = `${caracteristicasHtml}<div class="section-divider"><div class="section-title">🔌 Cargador</div><div class="grid-2">
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
    complementosStep.innerHTML = `${caracteristicasHtml}<div class="section-divider"><div class="section-title">🖱️ Mouse</div><div class="grid-2">
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
  complementosStep.innerHTML = caracteristicasHtml || '<div class="empty-box">Este tipo no tiene complementos. Puedes continuar a asignación.</div>';
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
}

function validarPaso() {
  if (step === 1 && !selectedTipo) return 'Selecciona un tipo de asset.';
  if (step === 2) {
    if (!numInventario.value.trim()) return 'Captura el número de inventario.';
    if ((SUBTIPOS[selectedTipo] || []).length && !subtipo.value) return 'Selecciona el subtipo.';
    if (!valorMarca()) return 'Selecciona o captura la marca.';
    if (!valorModelo()) return 'Selecciona o captura el modelo.';
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
    subtipo: subtipo?.value || '',
    numInventario: numInventario.value.trim(),
    marca: valorMarca(),
    modelo: valorModelo(),
    serie: serie.value.trim(),
    estado: estado.value,
    fechaCompra: fechaCompra.value.trim(),
    notas: notasConCaracteristicas(),
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
  if (!res.ok) {
    setMsg(`No se pudo cargar el asset ${edit} para edición.`);
    return;
  }
  const data = await res.json();
  editingAsset = data.asset;
  selectedTipo = editingAsset.tipo || 'Laptop';
  renderTipos();
  renderSubtipos(editingAsset.subtipo || '');
  renderMarcaModelo(editingAsset.marca || '', editingAsset.modelo || '');
  setValue('numInventario', editingAsset.numInventario);
  setValue('serie', editingAsset.serie);
  setValue('estado', editingAsset.estado || 'Activo');
  setValue('fechaCompra', editingAsset.fechaCompra);
  setValue('notas', parseNotas(editingAsset.notas).base);
  setValue('asignado', editingAsset.asignado);
  setValue('departamento', editingAsset.departamento);
  setValue('puesto', editingAsset.puesto);
  setValue('empleadoSearch', editingAsset.asignado);
  renderComplementos();
  step = 2;
  setWizardMode();
  mostrarStep();
}

function aplicarPrefillInventario() {
  if (editingAsset) return;
  const params = new URLSearchParams(location.search);
  if (params.get('inventariar') !== '1') return;
  selectedTipo = 'Laptop';
  renderTipos();
  renderSubtipos();
  renderMarcaModelo();
  setValue('serie', params.get('serie') || '');
  setValue('estado', 'Activo');
  const partes = [];
  const hostname = params.get('hostname') || '';
  const deviceId = params.get('device_id') || '';
  const ip = params.get('ip') || '';
  const ssid = params.get('ssid') || '';
  if (hostname) partes.push(`Hostname detectado: ${hostname}`);
  if (deviceId) partes.push(`Device ID: ${deviceId}`);
  if (ip) partes.push(`IP detectada: ${ip}`);
  if (ssid) partes.push(`SSID detectado: ${ssid}`);
  if (partes.length && !notas.value.trim()) setValue('notas', partes.join('
'));
  step = 2;
  setWizardMode();
  mostrarStep();
  inventarioHint.textContent = 'Datos precargados desde el sensor. Completa el número de inventario y guarda.';
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
  renderSubtipos();
  renderMarcaModelo();
  await cargarEdicion();
  aplicarPrefillInventario();
  sugerirInventario();
}

subtipo?.addEventListener('change', renderComplementos);
marcaSelect?.addEventListener('change', () => {
  marcaNueva.style.display = marcaSelect.value === NUEVO_VALOR ? '' : 'none';
  if (marcaSelect.value !== NUEVO_VALOR) marcaNueva.value = '';
  renderModelos(valorMarca(), '');
});
marcaNueva?.addEventListener('input', () => renderModelos(valorMarca(), valorModelo()));
modeloSelect?.addEventListener('change', () => {
  modeloNuevo.style.display = modeloSelect.value === NUEVO_VALOR ? '' : 'none';
  if (modeloSelect.value !== NUEVO_VALOR) modeloNuevo.value = '';
});
empleadoSearch.addEventListener('input', filtrarEmpleados);
empleadoSearch.addEventListener('focus', filtrarEmpleados);
document.addEventListener('click', (e) => {
  if (!e.target.closest('.search-wrap')) employeeResults.style.display = 'none';
});

renderTipos();
renderSubtipos();
renderMarcaModelo();
renderComplementos();
setWizardMode();
mostrarStep();
cargarDatos();
