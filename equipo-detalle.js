const SERVER = window.location.origin;
let TOKEN = localStorage.getItem('monitor_token');
let assetActual = null;

if (!TOKEN) location.href = '/login';

function headers() { return { Authorization: `Bearer ${TOKEN}` }; }
function logout() { localStorage.clear(); location.href = '/login'; }
function esc(v) { return String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }
function val(v) { return String(v ?? '').trim() || '—'; }
function norm(v) { return String(v || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/\s+/g, ' ').trim().toUpperCase(); }

function assetIdFromPath() {
  const parts = location.pathname.split('/').filter(Boolean);
  return decodeURIComponent(parts[1] || '');
}

function iconoTipo(tipo) {
  const t = norm(tipo);
  if (t.includes('LAPTOP')) return '💻';
  if (t.includes('DESKTOP') || t.includes('PC') || t.includes('COMPUTADORA')) return '🖥️';
  if (t.includes('MONITOR')) return '🖥';
  if (t.includes('UPS')) return '🔋';
  if (t.includes('RED') || t.includes('SWITCH') || t.includes('ROUTER') || t.includes('ACCESS POINT') || t.includes('FIREWALL')) return '🌐';
  return '📦';
}

function estadoInfo(estado) {
  const e = String(estado || 'Activo').trim() || 'Activo';
  const n = norm(e);
  if (n.includes('DISPONIBLE')) return { label: e, cls: 'badge-disponible' };
  if (n.includes('REPAR')) return { label: e, cls: 'badge-reparacion' };
  if (n.includes('FUERA')) return { label: e, cls: 'badge-fuera' };
  return { label: e, cls: 'badge-activo' };
}

function parseJson(value) {
  if (!value) return null;
  if (typeof value === 'object') return value;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function complementoTexto(id, marca, modelo, serie) {
  const main = String(id || '').trim();
  const detalle = [marca, modelo, serie].map((v) => String(v || '').trim()).filter(Boolean).join(' ');
  return [main, detalle].filter(Boolean).join(' · ');
}

function complementoJson(asset, key) {
  const item = parseJson(asset[key]);
  if (!item) return '';
  return complementoTexto(item.id || item.numInventario || item.num_inventario, item.marca, item.modelo, item.serie);
}

function dataRow(label, value) {
  return `<div class="data-row"><span class="label">${esc(label)}</span><span class="value">${esc(val(value))}</span></div>`;
}

function summaryCard(label, value) {
  return `<div class="summary-card"><span class="label">${esc(label)}</span><div class="value">${esc(val(value))}</div></div>`;
}

function compCard(title, text, lines = []) {
  if (!text && !lines.some(Boolean)) return '';
  const body = lines.length
    ? lines.filter(Boolean).map((line) => `<div>${esc(line)}</div>`).join('')
    : `<div>${esc(text)}</div>`;
  return `<div class="comp-card"><div class="comp-title">${title}</div><div class="comp-lines">${body}</div></div>`;
}

function boolText(v) {
  if (v === true) return 'Sí';
  if (v === false) return 'No';
  return '—';
}

function tipoNormalizado(asset) {
  return norm(asset.tipo);
}

function esLaptop(asset) {
  return tipoNormalizado(asset).includes('LAPTOP');
}

function esDesktop(asset) {
  const t = tipoNormalizado(asset);
  return t.includes('DESKTOP') || t.includes('PC') || t.includes('COMPUTADORA');
}

function esMonitor(asset) {
  return tipoNormalizado(asset).includes('MONITOR');
}

function esUpsRed(asset) {
  const t = tipoNormalizado(asset);
  return t.includes('UPS') || t === 'RED';
}

function renderComplementos(asset, monitor) {
  if (esMonitor(asset) || esUpsRed(asset)) return '';
  const cards = [];

  if (esLaptop(asset)) {
    const cargador = complementoTexto(asset.cargador_id, asset.cargador_marca, asset.cargador_modelo, asset.cargador_serie);
    if (cargador) {
      cards.push(compCard('🔌 Cargador', '', [
        asset.cargador_id ? `Inventario: ${asset.cargador_id}` : '',
        asset.cargador_marca ? `Marca: ${asset.cargador_marca}` : '',
        asset.cargador_modelo ? `Modelo: ${asset.cargador_modelo}` : '',
        asset.cargador_serie ? `Serie: ${asset.cargador_serie}` : '',
      ]));
    }
  }

  if (esDesktop(asset)) {
    const mouse = complementoJson(asset, 'mouse');
    if (mouse) cards.push(compCard('🖱️ Mouse', mouse));
    const teclado = complementoJson(asset, 'teclado');
    if (teclado) cards.push(compCard('⌨️ Teclado', teclado));
  }

  if ((esLaptop(asset) || esDesktop(asset)) && monitor) {
    cards.push(compCard('🖥 Monitor', '', [
      monitor.numInventario ? `Inventario: ${monitor.numInventario}` : '',
      complementoTexto('', monitor.marca, monitor.modelo, monitor.serie),
    ]));
  }
  if (!cards.length) return '';
  return `<section class="card"><h2>Complementos</h2><div class="complementos">${cards.join('')}</div></section>`;
}

function renderRedConectividad(asset) {
  if (esUpsRed(asset)) return '';
  return `<section class="card">
    <h2>Red y conectividad</h2>
    <div class="data-list">
      ${dataRow('IP', asset.ip)}
      ${dataRow('Hostname', asset.hostname)}
      ${dataRow('SSID', asset.ssid)}
      ${dataRow('Ubicación', asset.ubicacion)}
      ${dataRow('Último ping', asset.ultimoPing || asset.ultimo_ping || asset.actualizadoEn)}
    </div>
  </section>`;
}

function editar() {
  if (!assetActual) return;
  const id = encodeURIComponent(assetActual.numInventario || assetActual.id || '');
  location.href = `/equipos/nuevo?edit=${id}`;
}

function render(data) {
  const asset = data.asset;
  assetActual = asset;
  const monitor = data.monitor;
  const estado = estadoInfo(asset.estado);
  const title = asset.numInventario || asset.id || asset.serie || 'Asset';
  const subtitle = [asset.marca, asset.modelo, asset.serie ? `Serie ${asset.serie}` : ''].filter(Boolean).join(' · ');
  detailCard.innerHTML = `
    <div class="hero">
      <div class="asset-icon">${iconoTipo(asset.tipo)}</div>
      <div>
        <h1>${esc(title)}</h1>
        <div class="subtitle">${esc(subtitle || asset.tipo || 'Sin detalles técnicos')}</div>
      </div>
      <div class="hero-actions">
        <span class="badge ${estado.cls}">${esc(estado.label)}</span>
        <button class="btn primary" onclick="editar()">Editar</button>
      </div>
    </div>
    <div class="summary-grid">
      ${summaryCard('Asignado a', asset.asignado)}
      ${summaryCard('Departamento', asset.departamento)}
      ${summaryCard('Puesto', asset.puesto)}
      ${summaryCard('Fecha de compra', asset.fechaCompra)}
    </div>
    <div class="two-cols">
      ${renderRedConectividad(asset)}
      ${renderComplementos(asset, monitor)}
    </div>
    <section class="card">
      <h2>SO y agente</h2>
      <div class="agent-grid">
        ${summaryCard('Sistema operativo', asset.sistema)}
        ${summaryCard('Dentro de oficina', boolText(asset.dentro))}
        ${summaryCard('USB bloqueado', boolText(asset.usb_storage_blocked))}
        ${summaryCard('Device ID', asset.deviceId)}
      </div>
    </section>
  `;
}

async function cargar() {
  const id = assetIdFromPath();
  if (!id) {
    detailCard.innerHTML = '<div class="error">No se recibió num_inventario.</div>';
    return;
  }
  const res = await fetch(`${SERVER}/api/assets/by-inventario/${encodeURIComponent(id)}`, { headers: headers() });
  if (res.status === 401) { logout(); return; }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    detailCard.innerHTML = `<div class="error">${esc(err.detail || 'No se pudo cargar el asset')}</div>`;
    return;
  }
  render(await res.json());
}

cargar();
