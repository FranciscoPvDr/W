const SERVER = window.location.origin;
let TOKEN = localStorage.getItem('monitor_token');
const ROLE = localStorage.getItem('monitor_role') || 'ingeniero';
const IS_GESTOR = ROLE === 'super_admin' || ROLE === 'admin' || (localStorage.getItem('monitor_username') || '') === 'admin';

const TIPOS_CATALOGO = [
  { grupo: 'Cómputo', tipos: ['Laptop', 'Desktop', 'Monitor', 'Servidor'] },
  { grupo: 'Infraestructura / red', tipos: ['Switch', 'Access Point', 'Router', 'Firewall', 'NAS', 'UPS', 'Patch Panel'] },
  { grupo: 'Impresión', tipos: ['Impresora', 'Impresora de tickets', 'Plotter'] },
  { grupo: 'Periféricos y accesorios', tipos: ['Cargador', 'Dock', 'Mouse', 'Teclado'] },
  { grupo: 'Otros', tipos: ['Otro', 'Red'] },
];

const TIPO_A_CATEGORIA = {};
TIPOS_CATALOGO.forEach((g) => {
  const cat = g.grupo.includes('Infra') ? 'Infraestructura' : g.grupo.includes('Impres') ? 'Impresión' : g.grupo.includes('Perif') ? 'Periféricos' : g.grupo.includes('Cómputo') ? 'Cómputo' : 'Otros';
  g.tipos.forEach((t) => { TIPO_A_CATEGORIA[t] = cat; });
});
TIPO_A_CATEGORIA['Red'] = 'Infraestructura';

let assets = [];
let empleados = [];
let tipoActual = 'Todos';

if (!TOKEN) location.href = '/login';
if (IS_GESTOR) {
  const n = document.getElementById('nav-usuarios');
  if (n) n.style.display = '';
}

function headers() { return { Authorization: `Bearer ${TOKEN}` }; }
function jsonHeaders() { return { ...headers(), 'Content-Type': 'application/json' }; }
function logout() { localStorage.clear(); location.href = '/login'; }
function esc(v) { return String(v || '').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }
function norm(v) { return String(v || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/\s+/g, ' ').trim().toUpperCase(); }

function inferirCargadorDesdeLaptop(num) {
  const m = String(num || '').trim().toUpperCase().match(/^(.+)-LAP-(\d+)$/);
  return m ? `${m[1]}-CARG-${m[2]}` : '';
}

function inferirLaptopDesdeCargador(num) {
  const m = String(num || '').trim().toUpperCase().match(/^(.+)-CARG-(\d+)$/);
  return m ? `${m[1]}-LAP-${m[2]}` : '';
}

function initTiposSelect() {
  const sel = document.getElementById('tipo');
  if (!sel) return;
  sel.innerHTML = TIPOS_CATALOGO.map((g) =>
    `<optgroup label="${esc(g.grupo)}">${g.tipos.map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join('')}</optgroup>`
  ).join('');
}

function categoriaDeAsset(a) {
  const t = a.tipo || 'Otro';
  if (t === 'Red') return 'Infraestructura';
  return TIPO_A_CATEGORIA[t] || 'Otros';
}

function empByName(n) {
  const key = norm(n);
  return empleados.find((e) => norm(e.nombreCompleto) === key);
}

function asignacionOficial(a) {
  if (!norm(a.asignado)) return { asignado: '', departamento: '', puesto: '' };
  const e = empByName(a.asignado);
  return e
    ? { asignado: e.nombreCompleto, departamento: e.departamento || a.departamento || '', puesto: e.puesto || a.puesto || '' }
    : { asignado: a.asignado || '', departamento: a.departamento || '', puesto: a.puesto || '' };
}

function complementoTexto(id, marca, modelo, serie) {
  const main = String(id || '').trim();
  const detalle = [marca, modelo, serie].map((v) => String(v || '').trim()).filter(Boolean).join(' ');
  return [main, detalle].filter(Boolean).join(' · ');
}

function getJsonComplemento(a, key) {
  const value = a[key];
  if (!value) return null;
  if (typeof value === 'object') return value;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function complementoJsonTexto(item) {
  if (!item) return '';
  return complementoTexto(item.id || item.numInventario || item.num_inventario, item.marca, item.modelo, item.serie);
}

function estadoPill(estado) {
  const label = String(estado || 'Activo').trim() || 'Activo';
  const n = norm(label);
  let cls = 'status-activo';
  if (n.includes('DISPONIBLE')) cls = 'status-disponible';
  if (n.includes('REPAR')) cls = 'status-reparacion';
  if (n.includes('FUERA')) cls = 'status-fuera';
  return `<span class="status-pill ${cls}">${esc(label)}</span>`;
}

function tipoPill(a) {
  const t = a.tipo || 'Otro';
  const sub = a.subtipo ? ` · ${esc(a.subtipo)}` : '';
  const cat = categoriaDeAsset(a);
  return `<span class="pill">${esc(t)}${sub}</span><div class="muted" style="font-size:.72rem;margin-top:4px">${esc(cat)}</div>`;
}

function tabsLista() {
  const tiposEnBd = Array.from(new Set(assets.map((a) => a.tipo || 'Otro')));
  const cats = ['Todos', 'Cómputo', 'Infraestructura', 'Impresión', 'Periféricos'];
  const tabs = cats.map((c) => ({ id: c, label: c }));
  tiposEnBd.sort().forEach((t) => tabs.push({ id: t, label: t }));
  return tabs;
}

function pasaFiltroTipo(a) {
  if (tipoActual === 'Todos') return true;
  if (['Cómputo', 'Infraestructura', 'Impresión', 'Periféricos'].includes(tipoActual)) {
    return categoriaDeAsset(a) === tipoActual;
  }
  const t = a.tipo || 'Otro';
  if (tipoActual === 'Infraestructura' && t === 'Red') return true;
  return t === tipoActual;
}

function renderTabs() {
  const el = document.getElementById('tabs');
  if (!el) return;
  el.innerHTML = tabsLista().map((t) =>
    `<button type="button" class="tab ${t.id === tipoActual ? 'active' : ''}" onclick="tipoActual='${esc(t.id)}';renderTabs();render()">${esc(t.label)}</button>`
  ).join('');
}

function actualizarVinculoSugerido() {
  const hint = document.getElementById('vinculoHint');
  const btn = document.getElementById('btnCrearCargador');
  if (!document.getElementById('tipo') || !document.getElementById('numInventario')) return;
  const num = numInventario.value.trim().toUpperCase();
  const tipoVal = tipo.value;
  if (!hint) return;

  if (tipoVal === 'Laptop' && num) {
    const carg = inferirCargadorDesdeLaptop(num);
    if (carg) {
      const existe = assets.some((a) => norm(a.numInventario) === norm(carg));
      hint.textContent = existe
        ? `Cargador vinculado ${carg} ya está en inventario.`
        : `Cargador esperado: ${carg} (aún no registrado en BD).`;
      if (btn) btn.style.display = existe ? 'none' : 'inline-block';
      return;
    }
  }
  if (tipoVal === 'Cargador' && num && !parentInventario.value.trim()) {
    const lap = inferirLaptopDesdeCargador(num);
    if (lap) {
      parentInventario.value = lap;
      hint.textContent = `Vinculado automáticamente a ${lap}.`;
      if (btn) btn.style.display = 'none';
      return;
    }
  }
  hint.textContent = tipoVal === 'Cargador' ? 'Indica el equipo padre (ej. MC-LAP-001) o usa convención MC-CARG-###.' : '';
  if (btn) btn.style.display = 'none';
}

function actualizarSeccionesComplementos() {
  if (!document.getElementById('tipo')) return;
  const tipoVal = tipo.value;
  const cargador = document.getElementById('cargadorSection');
  if (cargador) cargador.style.display = tipoVal === 'Laptop' ? 'block' : 'none';
}

function prefillCargadorVinculado() {
  if (!document.getElementById('numInventario')) return;
  const num = numInventario.value.trim().toUpperCase();
  const carg = inferirCargadorDesdeLaptop(num);
  if (!carg) {
    alert('El número de inventario debe seguir el patrón MC-LAP-001 para sugerir cargador.');
    return;
  }
  limpiarAssetForm();
  assetFormTitle.textContent = 'Registrar cargador vinculado';
  tipo.value = 'Cargador';
  numInventario.value = carg;
  parentInventario.value = num;
  const lap = assets.find((a) => norm(a.numInventario) === norm(num));
  if (lap) {
    asignado.value = lap.asignado || '';
    departamento.value = lap.departamento || '';
    puesto.value = lap.puesto || '';
  }
  actualizarVinculoSugerido();
  actualizarSeccionesComplementos();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

async function cargar() {
  const [resAssets, resEmps] = await Promise.all([
    fetch(`${SERVER}/api/assets`, { headers: headers() }),
    fetch(`${SERVER}/api/empleados`, { headers: headers() }),
  ]);
  if (resAssets.status === 401) { logout(); return; }
  if (!resAssets.ok) {
    const err = await resAssets.json().catch(() => ({}));
    tbody.innerHTML = `<tr><td colspan="6">No se pudo cargar inventario: ${esc(err.detail || resAssets.status)}</td></tr>`;
    return;
  }
  const data = await resAssets.json();
  const empData = resEmps.ok ? await resEmps.json() : { empleados: [] };
  assets = data.assets || [];
  empleados = (empData.empleados || []).filter((e) => e.activo !== false);
  llenarEmpleados();
  renderTabs();
  render();
  if (!editarDesdeUrl()) precargarDesdeUrl();
  if ((data.omitidos || []).length) {
    msg.textContent = `Se omitieron ${data.omitidos.length} assets con datos inválidos.`;
  }
}

function llenarEmpleados() {
  if (!document.getElementById('asignado')) return;
  const opts = '<option value="">Sin asignar</option>' + empleados.map((e) =>
    `<option value="${esc(e.nombreCompleto)}">${e.nombreCompleto} · ${e.departamento || ''} · ${e.puesto || ''}</option>`
  ).join('');
  asignado.innerHTML = opts;
}

function precargarDesdeUrl() {
  const p = new URLSearchParams(location.search);
  if (!p.size) return;
  if (!document.getElementById('tipo')) return;
  const term = p.get('serie') || p.get('device_id') || p.get('hostname') || '';
  if (p.get('focus') === '1') {
    q.value = term;
    render();
    prefillMsg.textContent = term ? `Mostrando equipo detectado: ${term}` : 'Mostrando equipo detectado';
    return;
  }
  limpiarAssetForm();
  assetFormTitle.textContent = 'Inventariar equipo detectado';
  tipo.value = 'Laptop';
  serie.value = p.get('serie') || '';
  notas.value = '';
  q.value = serie.value;
  prefillMsg.textContent = `Equipo detectado por sensor${serie.value ? ` · Serie ${serie.value}` : ''}${p.get('hostname') ? ` · ${p.get('hostname')}` : ''}. Captura inventario y asignación.`;
  actualizarVinculoSugerido();
  actualizarSeccionesComplementos();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function empleadoOptions(actual) {
  const actualKey = norm(actual);
  return '<option value="">Sin asignar</option>' + empleados.map((e) =>
    `<option value="${esc(e.nombreCompleto)}" ${norm(e.nombreCompleto) === actualKey ? 'selected' : ''}>${e.nombreCompleto} · ${e.departamento || ''} · ${e.puesto || ''}</option>`
  ).join('');
}

function abrirDetalleAsset(id) {
  if (!id) return;
  location.href = `/equipos/${encodeURIComponent(id)}`;
}

function autocompletarAsset() {
  const e = empByName(asignado.value);
  departamento.value = e ? e.departamento || '' : '';
  puesto.value = e ? e.puesto || '' : '';
}

function aplicarEmpleado(id) {
  const e = empByName(document.getElementById(`asig-${id}`).value);
  if (e) {
    document.getElementById(`dep-${id}`).value = e.departamento || '';
    document.getElementById(`pto-${id}`).value = e.puesto || '';
  } else {
    document.getElementById(`dep-${id}`).value = '';
    document.getElementById(`pto-${id}`).value = '';
  }
}

function render() {
  const term = (q.value || '').toLowerCase();
  let rows = assets.filter(pasaFiltroTipo).filter((a) => JSON.stringify(a).toLowerCase().includes(term));
  tbody.innerHTML = rows.map((a) => {
    const asig = asignacionOficial(a);
    const detalleId = a.numInventario || a.id;
    return `<tr ${detalleId ? `onclick="abrirDetalleAsset('${esc(detalleId)}')"` : ''} style="cursor:${detalleId ? 'pointer' : 'default'}">
      <td><strong>${esc(a.numInventario || 'Sin inventario')}</strong><div class="muted">Serie: ${esc(a.serie || '—')} · ${esc(a.marca || '')} ${esc(a.modelo || '')}</div></td>
      <td>${tipoPill(a)}</td>
      <td>${estadoPill(a.estado)}</td>
      <td>${asig.asignado || '<span class="muted">Sin asignar</span>'}<div class="muted">${esc(asig.departamento || '')} ${asig.puesto ? `· ${esc(asig.puesto)}` : ''}</div></td>
      <td onclick="event.stopPropagation()">
        <button class="btn" onclick="editarAsset('${a.id}')">Editar</button>
        <button class="btn danger" onclick="eliminar('${a.id}')">Eliminar</button>
      </td>
    </tr>`;
  }).join('') || '<tr><td colspan="5">Sin assets</td></tr>';
}

function assetPayload() {
  return {
    tipo: tipo.value,
    numInventario: numInventario.value.trim(),
    serie: serie.value.trim(),
    subtipo: subtipo.value.trim(),
    parentInventario: parentInventario.value.trim(),
    marca: marca.value.trim(),
    modelo: modelo.value.trim(),
    fechaCompra: fechaCompra.value.trim(),
    notas: notas.value.trim(),
    cargador_id: tipo.value === 'Laptop' ? cargador_id.value.trim() : '',
    cargador_marca: tipo.value === 'Laptop' ? cargador_marca.value.trim() : '',
    cargador_modelo: tipo.value === 'Laptop' ? cargador_modelo.value.trim() : '',
    cargador_serie: tipo.value === 'Laptop' ? cargador_serie.value.trim() : '',
    asignado: asignado.value.trim(),
    departamento: departamento.value.trim(),
    puesto: puesto.value.trim(),
  };
}

function limpiarAssetForm() {
  assetFormTitle.textContent = 'Agregar asset';
  editAssetId.value = '';
  ['numInventario', 'serie', 'subtipo', 'parentInventario', 'marca', 'modelo', 'fechaCompra', 'notas', 'cargador_id', 'cargador_marca', 'cargador_modelo', 'cargador_serie', 'departamento', 'puesto'].forEach((id) => { window[id].value = ''; });
  asignado.value = '';
  tipo.value = 'Laptop';
  msg.textContent = '';
  const hint = document.getElementById('vinculoHint');
  if (hint) hint.textContent = '';
  const btn = document.getElementById('btnCrearCargador');
  if (btn) btn.style.display = 'none';
  actualizarSeccionesComplementos();
}

function editarAsset(id) {
  if (!document.getElementById('editAssetId')) {
    const a = assets.find((x) => x.id === id);
    location.href = `/equipos/nuevo${a?.numInventario ? `?edit=${encodeURIComponent(a.numInventario)}` : ''}`;
    return;
  }
  const a = assets.find((x) => x.id === id);
  if (!a) return;
  assetFormTitle.textContent = 'Editar asset';
  editAssetId.value = a.id;
  if ([...tipo.options].some((o) => o.value === a.tipo)) tipo.value = a.tipo;
  else tipo.value = 'Otro';
  subtipo.value = a.subtipo || '';
  numInventario.value = a.numInventario || '';
  parentInventario.value = a.parentInventario || '';
  serie.value = a.serie || '';
  marca.value = a.marca || '';
  modelo.value = a.modelo || '';
  fechaCompra.value = a.fechaCompra || '';
  notas.value = a.notas || '';
  cargador_id.value = a.cargador_id || '';
  cargador_marca.value = a.cargador_marca || '';
  cargador_modelo.value = a.cargador_modelo || '';
  cargador_serie.value = a.cargador_serie || '';
  const asig = asignacionOficial(a);
  asignado.value = asig.asignado || '';
  departamento.value = asig.departamento || '';
  puesto.value = asig.puesto || '';
  actualizarVinculoSugerido();
  actualizarSeccionesComplementos();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function editarDesdeUrl() {
  const edit = new URLSearchParams(location.search).get('edit');
  if (!document.getElementById('editAssetId')) return false;
  if (!edit) return false;
  if (!assets.length) {
    setTimeout(editarDesdeUrl, 300);
    return false;
  }
  const asset = assets.find((a) => norm(a.numInventario) === norm(edit) || norm(a.id) === norm(edit));
  if (!asset) return false;
  editarAsset(asset.id);
  prefillMsg.textContent = `Editando asset ${asset.numInventario || asset.id}`;
  history.replaceState(null, '', '/equipos');
  return true;
}

async function guardarAsset() {
  const payload = assetPayload();
  if (!payload.serie && !payload.numInventario) {
    alert('Captura serie o número de inventario');
    return;
  }
  const id = editAssetId.value;
  const res = await fetch(id ? `${SERVER}/api/assets/${encodeURIComponent(id)}` : `${SERVER}/api/assets`, {
    method: id ? 'PATCH' : 'POST',
    headers: jsonHeaders(),
    body: JSON.stringify(payload),
  });
  const err = await res.json().catch(() => ({}));
  msg.textContent = res.ok ? 'Guardado en Supabase' : (err.detail || 'No se pudo guardar');
  if (res.ok) { limpiarAssetForm(); cargar(); }
}

async function asignar(id) {
  const seleccionado = document.getElementById(`asig-${id}`).value.trim();
  const e = empByName(seleccionado);
  const payload = {
    asignado: e ? e.nombreCompleto : seleccionado,
    departamento: document.getElementById(`dep-${id}`).value.trim(),
    puesto: document.getElementById(`pto-${id}`).value.trim(),
  };
  const res = await fetch(`${SERVER}/api/assets/${encodeURIComponent(id)}/asignacion`, { method: 'PATCH', headers: jsonHeaders(), body: JSON.stringify(payload) });
  if (!res.ok) alert('No se pudo asignar');
  cargar();
}

async function desasignar(id) {
  if (!confirm('¿Desasignar este asset?')) return;
  const res = await fetch(`${SERVER}/api/assets/${encodeURIComponent(id)}/asignacion`, {
    method: 'PATCH', headers: jsonHeaders(), body: JSON.stringify({ asignado: '', departamento: '', puesto: '' }),
  });
  if (!res.ok) alert('No se pudo desasignar');
  cargar();
}

async function eliminar(id) {
  if (!confirm('¿Eliminar asset de Supabase?')) return;
  const res = await fetch(`${SERVER}/api/assets/${encodeURIComponent(id)}`, { method: 'DELETE', headers: headers() });
  if (!res.ok) alert('No se pudo eliminar');
  cargar();
}

document.getElementById('tipo')?.addEventListener('change', () => {
  actualizarVinculoSugerido();
  actualizarSeccionesComplementos();
});
initTiposSelect();
actualizarSeccionesComplementos();
cargar();
