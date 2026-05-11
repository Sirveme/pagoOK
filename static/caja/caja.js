// ============================================================
// pagoOK Caja v4 - Catálogo emergente + Pago parcial + Online/Offline
// ============================================================

(function() {
  'use strict';

  // ============================================================
  // ESTADO
  // ============================================================
  const estado = {
    pantalla: 'login',
    ruc: '',
    pin: '',
    empresa: null,
    localActual: null,
    vendedor: null,
    items: [], // [{cantidad, nombre, precioUnit?, subtotal?, calculado?}]
    total: 0,
    metodosPago: [], // [{metodo: 'yape'|'plin'|'efectivo', monto, nOperacion?, foto?}]
    metodoActual: null,
    nOperacion: '',
    foto: null,
    montoPagoActual: 0,
    cliente: { tipoDoc: 'ninguno', numero: '', nombre: '' },
    historial: [],
    catalogo: [], // [{nombre, alias, precioUnit, veces, ultimaVez}]
    sonidoActivo: true,
    correlativos: {},
    itemEditandoIdx: -1,
    online: navigator.onLine,
  };

  // ============================================================
  // MOCK
  // ============================================================
  const EMPRESAS_DEMO = {
    '20615446565': {
      nombre: 'Pollería Bolognesi S.A.C.',
      tieneCDT: true,
      aplicaIGV: false, // Iquitos = Amazonía, sin IGV
      ciudad: 'Iquitos - Loreto',
      locales: [
        {
          id: 'local_1',
          direccion: 'Av. Bolognesi 346',
          serieBase: 346,
          vendedores: [
            { alias: 'vendedor1', pin: '1234', nombre: 'Carlos', serieB: 'B346', serieF: 'F346' },
            { alias: 'vendedor2', pin: '5678', nombre: 'María', serieB: 'B347', serieF: 'F347' },
          ],
        },
      ],
    },
    '99999999999': {
      nombre: 'Negocio Demo',
      tieneCDT: false,
      aplicaIGV: true,
      ciudad: 'Lima - Lima',
      locales: [
        {
          id: 'local_demo',
          direccion: 'Calle Demo 100',
          serieBase: null,
          vendedores: [
            { alias: 'vendedor1', pin: '0000', nombre: 'Demo', serieB: 'B000', serieF: 'F000' },
          ],
        },
      ],
    },
  };

  // ============================================================
  // AUDIO
  // ============================================================
  let audioCtx = null;
  function getAudioCtx() {
    if (!audioCtx) {
      try { audioCtx = new (window.AudioContext || window.webkitAudioContext)(); }
      catch (e) {}
    }
    return audioCtx;
  }

  function tono(freq, dur, tipo = 'sine', vol = 0.15) {
    if (!estado.sonidoActivo) return;
    const ctx = getAudioCtx();
    if (!ctx) return;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);
    osc.type = tipo;
    osc.frequency.setValueAtTime(freq, ctx.currentTime);
    gain.gain.setValueAtTime(0, ctx.currentTime);
    gain.gain.linearRampToValueAtTime(vol, ctx.currentTime + 0.005);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + dur);
    osc.start(ctx.currentTime); osc.stop(ctx.currentTime + dur + 0.05);
  }

  function sonidoSwoosh() {
    if (!estado.sonidoActivo) return;
    const ctx = getAudioCtx();
    if (!ctx) return;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    const filter = ctx.createBiquadFilter();
    osc.connect(filter); filter.connect(gain); gain.connect(ctx.destination);
    osc.type = 'sine';
    filter.type = 'lowpass';
    filter.frequency.setValueAtTime(2000, ctx.currentTime);
    filter.frequency.exponentialRampToValueAtTime(400, ctx.currentTime + 0.18);
    osc.frequency.setValueAtTime(800, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(200, ctx.currentTime + 0.18);
    gain.gain.setValueAtTime(0, ctx.currentTime);
    gain.gain.linearRampToValueAtTime(0.08, ctx.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.18);
    osc.start(ctx.currentTime); osc.stop(ctx.currentTime + 0.2);
  }

  function sonidoExito() {
    if (!estado.sonidoActivo) return;
    setTimeout(() => tono(523.25, 0.15, 'triangle', 0.18), 0);
    setTimeout(() => tono(659.25, 0.15, 'triangle', 0.18), 100);
    setTimeout(() => tono(783.99, 0.30, 'triangle', 0.20), 200);
    setTimeout(() => tono(1046.50, 0.40, 'sine', 0.10), 240);
  }

  function sonidoAdvertencia() {
    if (!estado.sonidoActivo) return;
    setTimeout(() => tono(523.25, 0.20, 'sine', 0.18), 0);
    setTimeout(() => tono(415.30, 0.30, 'sine', 0.18), 200);
  }

  function sonidoError() {
    if (!estado.sonidoActivo) return;
    const ctx = getAudioCtx();
    if (!ctx) return;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);
    osc.type = 'sawtooth';
    osc.frequency.setValueAtTime(220, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(110, ctx.currentTime + 0.4);
    gain.gain.setValueAtTime(0, ctx.currentTime);
    gain.gain.linearRampToValueAtTime(0.15, ctx.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.4);
    osc.start(ctx.currentTime); osc.stop(ctx.currentTime + 0.45);
  }

  function sonidoTap() {
    if (!estado.sonidoActivo) return;
    tono(1500, 0.04, 'sine', 0.08);
  }

  // ============================================================
  // PERSISTENCIA
  // ============================================================
  function cargarEstado() {
    try {
      const saved = localStorage.getItem('pagook_caja_v4');
      if (saved) {
        const data = JSON.parse(saved);
        estado.catalogo = data.catalogo || [];
        estado.historial = data.historial || [];
        estado.sonidoActivo = data.sonidoActivo !== false;
        estado.correlativos = data.correlativos || {};
      }
      const ultimoRuc = localStorage.getItem('pagook_ultimo_ruc');
      if (ultimoRuc) {
        document.getElementById('input-ruc').value = ultimoRuc;
        validarRucEnVivo(ultimoRuc);
      } else {
        document.getElementById('input-ruc').value = '20615446565';
        validarRucEnVivo('20615446565');
      }
    } catch (e) { console.warn('No se pudo cargar', e); }
  }

  function guardarEstado() {
    try {
      localStorage.setItem('pagook_caja_v4', JSON.stringify({
        catalogo: estado.catalogo,
        historial: estado.historial,
        sonidoActivo: estado.sonidoActivo,
        correlativos: estado.correlativos,
      }));
    } catch (e) {}
  }

  function guardarRuc(ruc) {
    try { localStorage.setItem('pagook_ultimo_ruc', ruc); } catch (e) {}
  }

  // ============================================================
  // CONEXIÓN ONLINE/OFFLINE
  // ============================================================
  function actualizarConexion() {
    estado.online = navigator.onLine;
    const bar = document.getElementById('conexion-bar');
    const topbar = document.getElementById('topbar-dictar');
    const texto = document.getElementById('conexion-texto');
    if (!bar) return;
    if (estado.online) {
      bar.classList.remove('offline');
      if (topbar) topbar.classList.remove('offline');
      texto.textContent = 'En línea';
    } else {
      bar.classList.add('offline');
      if (topbar) topbar.classList.add('offline');
      texto.textContent = 'Sin conexión';
    }
  }

  window.addEventListener('online', actualizarConexion);
  window.addEventListener('offline', actualizarConexion);

  // ============================================================
  // NAVEGACIÓN
  // ============================================================
  const PANTALLAS_CON_SWOOSH = ['login', 'dictar', 'verificar', 'boleta'];

  function irA(nombre, opciones = {}) {
    const actual = document.querySelector('.pantalla.activa');
    const proxima = document.getElementById('p-' + nombre);
    if (!proxima || actual === proxima) return;
    const { sentido = 'derecha', conSwoosh = null } = opciones;
    const haceSonido = conSwoosh !== null
      ? conSwoosh
      : (PANTALLAS_CON_SWOOSH.includes(nombre) || PANTALLAS_CON_SWOOSH.includes(estado.pantalla));
    if (haceSonido) sonidoSwoosh();
    if (actual) {
      if (sentido === 'derecha') actual.classList.add('saliente-izquierda');
      actual.classList.remove('activa');
    }
    proxima.classList.remove('saliente-izquierda', 'entrante-izquierda');
    if (sentido === 'izquierda') proxima.classList.add('entrante-izquierda');
    void proxima.offsetWidth;
    proxima.classList.remove('entrante-izquierda');
    proxima.classList.add('activa');
    estado.pantalla = nombre;
    setTimeout(() => {
      document.querySelectorAll('.pantalla:not(.activa)').forEach(p => {
        p.classList.remove('saliente-izquierda', 'entrante-izquierda');
      });
    }, 500);
  }

  // ============================================================
  // TOAST
  // ============================================================
  let toastTimer = null;
  function toast(mensaje, tipo = '') {
    const el = document.getElementById('toast');
    el.textContent = mensaje;
    el.className = 'toast visible ' + tipo;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('visible'), 2800);
  }

  // ============================================================
  // FORMATO
  // ============================================================
  function fmt(n) {
    if (n === undefined || n === null || isNaN(n)) return 'S/ 0';
    return 'S/ ' + (n % 1 === 0 ? n.toFixed(0) : n.toFixed(2));
  }

  function fmt2(n) {
    if (n === undefined || n === null || isNaN(n)) return '0.00';
    return n.toFixed(2);
  }

  // ============================================================
  // LOGIN
  // ============================================================
  function validarRucEnVivo(ruc) {
    const display = document.getElementById('empresa-nombre-display');
    const localesGrupo = document.getElementById('locales-grupo');
    const select = document.getElementById('select-local');

    if (ruc.length !== 11) {
      display.classList.add('oculto');
      localesGrupo.classList.add('oculto');
      estado.empresa = null;
      estado.localActual = null;
      return;
    }
    const empresa = EMPRESAS_DEMO[ruc];
    if (!empresa) {
      display.classList.add('oculto');
      localesGrupo.classList.add('oculto');
      estado.empresa = null;
      estado.localActual = null;
      return;
    }
    estado.empresa = empresa;
    document.getElementById('empresa-nombre-txt').textContent = empresa.nombre;
    display.classList.remove('oculto');
    if (empresa.locales.length > 1) {
      select.innerHTML = '<option value="">Selecciona el local...</option>';
      empresa.locales.forEach(l => {
        const opt = document.createElement('option');
        opt.value = l.id;
        opt.textContent = l.direccion;
        select.appendChild(opt);
      });
      localesGrupo.classList.remove('oculto');
    } else {
      estado.localActual = empresa.locales[0];
      localesGrupo.classList.add('oculto');
    }
  }

  document.getElementById('input-ruc').addEventListener('input', (e) => {
    const ruc = e.target.value.replace(/\D/g, '').slice(0, 11);
    e.target.value = ruc;
    validarRucEnVivo(ruc);
  });

  document.getElementById('input-ruc').addEventListener('focus', (e) => {
    setTimeout(() => e.target.select(), 50);
  });

  document.getElementById('select-local').addEventListener('change', (e) => {
    const localId = e.target.value;
    if (estado.empresa && localId) {
      estado.localActual = estado.empresa.locales.find(l => l.id === localId);
    } else {
      estado.localActual = null;
    }
  });

  function actualizarPinDisplay() {
    document.querySelectorAll('.pin-dot').forEach((dot, i) => {
      if (i < estado.pin.length) dot.classList.add('lleno');
      else dot.classList.remove('lleno');
    });
  }

  function intentarLogin() {
    const ruc = document.getElementById('input-ruc').value.trim();
    const pin = estado.pin;
    const errorEl = document.getElementById('login-error');
    if (!estado.empresa) {
      errorEl.textContent = 'RUC no registrado';
      errorEl.classList.remove('oculto');
      sonidoError();
      return;
    }
    if (estado.empresa.locales.length > 1 && !estado.localActual) {
      errorEl.textContent = 'Selecciona el local primero';
      errorEl.classList.remove('oculto');
      sonidoError();
      return;
    }
    if (pin.length !== 4) {
      errorEl.textContent = 'El PIN debe tener 4 dígitos';
      errorEl.classList.remove('oculto');
      sonidoError();
      return;
    }
    const vendedor = estado.localActual.vendedores.find(v => v.pin === pin);
    if (!vendedor) {
      errorEl.textContent = 'PIN incorrecto';
      errorEl.classList.remove('oculto');
      estado.pin = '';
      actualizarPinDisplay();
      sonidoError();
      return;
    }
    errorEl.classList.add('oculto');
    estado.ruc = ruc;
    estado.vendedor = vendedor;
    guardarRuc(ruc);
    document.getElementById('vendedor-nombre').textContent = vendedor.nombre;
    document.getElementById('local-display').textContent = estado.empresa.nombre + ' · ' + vendedor.serieB;
    estado.pin = '';
    actualizarPinDisplay();
    actualizarConexion();
    irA('dictar');
    actualizarSugerencias();
    setTimeout(() => toast('Bienvenido ' + vendedor.nombre, 'exito'), 200);
  }

  document.querySelectorAll('.tecla').forEach(btn => {
    btn.addEventListener('click', () => {
      sonidoTap();
      const num = btn.dataset.num;
      const accion = btn.dataset.accion;
      if (num !== undefined) {
        if (estado.pin.length < 4) {
          estado.pin += num;
          actualizarPinDisplay();
          if (estado.pin.length === 4) setTimeout(intentarLogin, 220);
        }
      } else if (accion === 'borrar') {
        estado.pin = estado.pin.slice(0, -1);
        actualizarPinDisplay();
      } else if (accion === 'entrar') {
        intentarLogin();
      }
    });
  });

  // ============================================================
  // CATÁLOGO EMERGENTE - sugerencias
  // ============================================================
  function actualizarSugerencias() {
    const cont = document.getElementById('sugerencias');
    const chips = document.getElementById('sugerencias-chips');
    if (estado.catalogo.length === 0) {
      cont.classList.add('oculto');
      return;
    }
    // Top 5 más vendidos
    const top = [...estado.catalogo].sort((a, b) => b.veces - a.veces).slice(0, 5);
    chips.innerHTML = '';
    top.forEach(p => {
      const chip = document.createElement('button');
      chip.className = 'sugerencia-chip';
      chip.type = 'button';
      let txt = p.nombre;
      if (p.precioUnit) txt += `<span class="sugerencia-chip-precio">S/${fmt2(p.precioUnit)}</span>`;
      chip.innerHTML = txt;
      chip.addEventListener('click', () => {
        sonidoTap();
        agregarItemDelCatalogo(p);
      });
      chips.appendChild(chip);
    });
    cont.classList.remove('oculto');
  }

  function agregarItemDelCatalogo(prod) {
    // Buscar si ya está en items actuales
    const existe = estado.items.find(i => i.nombre.toLowerCase() === prod.nombre.toLowerCase());
    if (existe) {
      existe.cantidad++;
      if (prod.precioUnit && !existe.precioUnit) existe.precioUnit = prod.precioUnit;
    } else {
      estado.items.push({
        cantidad: 1,
        nombre: prod.nombre,
        precioUnit: prod.precioUnit || null,
      });
    }
    recalcularTotal();
    if (estado.items.length === 1) {
      // Primer item agregado, ir a items
      renderItems();
      irA('items');
    } else {
      toast(`+ ${prod.nombre}`, 'exito');
    }
  }

  // ============================================================
  // PARSER
  // ============================================================
  const NUM_PALABRAS = {
    'un': 1, 'una': 1, 'uno': 1,
    'dos': 2, 'tres': 3, 'cuatro': 4, 'cinco': 5,
    'seis': 6, 'siete': 7, 'ocho': 8, 'nueve': 9, 'diez': 10,
    'once': 11, 'doce': 12, 'trece': 13, 'catorce': 14, 'quince': 15,
    'dieciseis': 16, 'diecisiete': 17, 'dieciocho': 18, 'diecinueve': 19,
    'veinte': 20, 'treinta': 30,
    'media': 0.5, 'medio': 0.5,
  };

  function tokenizar(texto) {
    const palabras = texto.toLowerCase().split(/\s+/).filter(p => p.length > 0);
    return palabras.map(p => {
      const limpia = p.replace(/[,;.]+$/, '');
      if (/^\d+(?:[.,]\d+)?$/.test(limpia)) {
        return { tipo: 'num', valor: parseFloat(limpia.replace(',', '.')), texto: limpia };
      }
      if (NUM_PALABRAS[limpia] !== undefined) {
        return { tipo: 'num', valor: NUM_PALABRAS[limpia], texto: limpia };
      }
      return { tipo: 'palabra', valor: limpia, texto: limpia };
    });
  }

  function parsearVenta(texto) {
    texto = texto.trim();
    if (!texto) return { items: [], total: 0 };
    let total = 0;
    const patronTotalExplicito = /(?:total|son|=|s\/\s*)\s*(\d+(?:[.,]\d{1,2})?)\s*(?:soles?|s\/)?\s*$/i;
    let matchTotal = texto.match(patronTotalExplicito);
    if (matchTotal) {
      total = parseFloat(matchTotal[1].replace(',', '.'));
      texto = texto.substring(0, matchTotal.index).trim();
    } else {
      const patronImplicito = /(\d+(?:[.,]\d{1,2})?)\s*(?:soles?|s\/)?\s*$/i;
      const matchImpl = texto.match(patronImplicito);
      if (matchImpl) {
        const candidato = parseFloat(matchImpl[1].replace(',', '.'));
        if (candidato >= 10) {
          total = candidato;
          texto = texto.substring(0, matchImpl.index).trim();
        }
      }
    }
    texto = texto.replace(/[,;]/g, ' ');
    const tokens = tokenizar(texto);
    const STOP_WORDS = ['y', 'de', 'con', 'mas', 'más', 'el', 'la', 'los', 'las'];
    const tokensFiltered = tokens.filter(t => {
      if (t.tipo === 'palabra' && STOP_WORDS.includes(t.valor)) return false;
      return true;
    });
    const items = [];
    let actual = null;
    for (let i = 0; i < tokensFiltered.length; i++) {
      const t = tokensFiltered[i];
      if (t.tipo === 'num') {
        if (actual && actual.nombre.length > 0) items.push(actual);
        actual = { cantidad: t.valor, nombre: '' };
      } else {
        if (!actual) actual = { cantidad: 1, nombre: '' };
        actual.nombre += (actual.nombre ? ' ' : '') + t.valor;
      }
    }
    if (actual && actual.nombre.length > 0) items.push(actual);
    if (items.length === 0) items.push({ cantidad: 1, nombre: 'Venta' });
    items.forEach(item => {
      if (item.nombre.length > 0) {
        item.nombre = item.nombre.charAt(0).toUpperCase() + item.nombre.slice(1);
      } else {
        item.nombre = 'Item';
      }
      // Buscar precio en catálogo
      const enCat = estado.catalogo.find(c => c.nombre.toLowerCase() === item.nombre.toLowerCase());
      if (enCat && enCat.precioUnit) {
        item.precioUnit = enCat.precioUnit;
      }
    });
    return { items, total };
  }

  // ============================================================
  // CÁLCULO INTELIGENTE: si hay 1 incógnita, resolver
  // ============================================================
  function recalcularTotal() {
    // Si total fue fijado manualmente, no recalcular automáticamente
    // EXCEPTO: si hay 1 item sin precio, calcularlo
    const sinPrecio = estado.items.filter(i => !i.precioUnit);

    if (sinPrecio.length === 0) {
      // Todos tienen precio: total = suma
      let suma = 0;
      estado.items.forEach(i => {
        i.subtotal = i.cantidad * i.precioUnit;
        i.calculado = false;
        suma += i.subtotal;
      });
      estado.total = Math.round(suma * 100) / 100;
    } else if (sinPrecio.length === 1 && estado.total > 0) {
      // 1 incógnita y total fijado: resolverla
      let sumaConocidos = 0;
      estado.items.forEach(i => {
        if (i.precioUnit) {
          i.subtotal = i.cantidad * i.precioUnit;
          i.calculado = false;
          sumaConocidos += i.subtotal;
        }
      });
      const incognita = sinPrecio[0];
      const restante = estado.total - sumaConocidos;
      if (restante > 0 && incognita.cantidad > 0) {
        incognita.precioUnit = Math.round((restante / incognita.cantidad) * 100) / 100;
        incognita.subtotal = restante;
        incognita.calculado = true;
      } else {
        incognita.subtotal = null;
      }
    }
    // Si hay 2+ incógnitas, el total se respeta tal cual lo puso el vendedor
    // Los items sin precio quedan sin subtotal
  }

  // ============================================================
  // RENDER ITEMS
  // ============================================================
  function renderItems() {
    const lista = document.getElementById('items-lista');
    lista.innerHTML = '';
    estado.items.forEach((item, idx) => {
      const li = document.createElement('li');
      li.className = 'item';
      li.dataset.idx = idx;

      let infoHtml = `<div class="item-nombre">${escapeHtml(item.nombre)}</div>`;
      if (item.precioUnit) {
        const calc = item.calculado ? 'calculado' : '';
        const calcLabel = item.calculado ? ' (calculado)' : '';
        infoHtml += `<div class="item-precio-unit ${calc}">S/ ${fmt2(item.precioUnit)} c/u${calcLabel}</div>`;
      } else {
        infoHtml += `<div class="item-precio-unit">Sin precio · toca para editar</div>`;
      }

      const subHtml = item.precioUnit
        ? `<span class="item-subtotal">S/ ${fmt2(item.cantidad * item.precioUnit)}</span>`
        : `<span class="item-edit-icono">›</span>`;

      li.innerHTML = `
        <div class="item-cantidad-box">${item.cantidad}</div>
        <div class="item-info">${infoHtml}</div>
        ${subHtml}
      `;
      li.addEventListener('click', () => {
        sonidoTap();
        abrirModalItem(idx);
      });
      lista.appendChild(li);
    });
    actualizarTotalDisplay();
    document.getElementById('total-hint').classList.toggle('oculto', estado.total > 0);
  }

  function actualizarTotalDisplay() {
    document.getElementById('items-total-btn').textContent = fmt(estado.total);
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // ============================================================
  // MODAL DE ITEM
  // ============================================================
  function abrirModalItem(idx) {
    const item = estado.items[idx];
    if (!item) return;
    estado.itemEditandoIdx = idx;
    document.getElementById('modal-item-nombre').value = item.nombre;
    document.getElementById('modal-item-cantidad').value = item.cantidad;
    document.getElementById('modal-item-precio').value = item.precioUnit ? fmt2(item.precioUnit) : '';
    actualizarSubtotalModal();
    document.getElementById('modal-item').classList.remove('oculto');
    setTimeout(() => document.getElementById('modal-item-nombre').focus(), 100);
  }

  function actualizarSubtotalModal() {
    const cant = parseFloat(document.getElementById('modal-item-cantidad').value.replace(',', '.'));
    const precio = parseFloat(document.getElementById('modal-item-precio').value.replace(',', '.'));
    const display = document.getElementById('modal-subtotal-display');
    if (!isNaN(cant) && !isNaN(precio) && precio > 0) {
      display.textContent = `Subtotal: S/ ${fmt2(cant * precio)}`;
    } else {
      display.textContent = 'Subtotal: sin precio unitario';
    }
  }

  ['modal-item-cantidad', 'modal-item-precio'].forEach(id => {
    document.getElementById(id).addEventListener('input', actualizarSubtotalModal);
  });

  document.getElementById('btn-modal-item-cancelar').addEventListener('click', () => {
    sonidoTap();
    document.getElementById('modal-item').classList.add('oculto');
  });

  document.getElementById('btn-modal-item-eliminar').addEventListener('click', () => {
    sonidoTap();
    if (estado.itemEditandoIdx >= 0) {
      estado.items.splice(estado.itemEditandoIdx, 1);
      recalcularTotal();
      renderItems();
    }
    document.getElementById('modal-item').classList.add('oculto');
  });

  document.getElementById('btn-modal-item-aceptar').addEventListener('click', () => {
    sonidoTap();
    const idx = estado.itemEditandoIdx;
    const item = estado.items[idx];
    if (!item) return;
    const nombre = document.getElementById('modal-item-nombre').value.trim();
    const cant = parseFloat(document.getElementById('modal-item-cantidad').value.replace(',', '.'));
    const precioStr = document.getElementById('modal-item-precio').value.trim();
    const precio = precioStr ? parseFloat(precioStr.replace(',', '.')) : null;
    if (!nombre) { sonidoError(); toast('El nombre no puede estar vacío', 'error'); return; }
    if (isNaN(cant) || cant <= 0) { sonidoError(); toast('Cantidad inválida', 'error'); return; }
    item.nombre = nombre;
    item.cantidad = cant;
    item.precioUnit = (precio !== null && !isNaN(precio) && precio > 0) ? precio : null;
    item.calculado = false;
    recalcularTotal();
    renderItems();
    document.getElementById('modal-item').classList.add('oculto');
  });

  // Total modal
  document.getElementById('items-total-btn').addEventListener('click', () => {
    sonidoTap();
    const modal = document.getElementById('modal-total');
    const input = document.getElementById('input-total-edit');
    input.value = estado.total > 0 ? estado.total.toString() : '';
    modal.classList.remove('oculto');
    setTimeout(() => { input.focus(); input.select(); }, 100);
  });

  document.getElementById('btn-total-cancelar').addEventListener('click', () => {
    document.getElementById('modal-total').classList.add('oculto');
  });

  document.getElementById('btn-total-aceptar').addEventListener('click', () => {
    const valor = parseFloat(document.getElementById('input-total-edit').value.replace(',', '.'));
    if (isNaN(valor) || valor < 0.5) {
      sonidoError();
      toast('Total inválido', 'error');
      return;
    }
    estado.total = valor;
    recalcularTotal();
    renderItems();
    document.getElementById('modal-total').classList.add('oculto');
    sonidoTap();
  });

  document.getElementById('input-total-edit').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') document.getElementById('btn-total-aceptar').click();
    if (e.key === 'Escape') document.getElementById('btn-total-cancelar').click();
  });

  // Procesar
  document.getElementById('btn-procesar').addEventListener('click', () => {
    const texto = document.getElementById('texto-venta').value.trim();
    if (!texto) {
      sonidoError();
      toast('Escribe o dicta la venta primero', 'error');
      return;
    }
    const parsed = parsearVenta(texto);
    estado.items = parsed.items;
    estado.total = parsed.total;
    recalcularTotal();
    renderItems();
    if (parsed.total === 0) {
      toast('Falta el total — toca el monto', '');
    }
    irA('items');
  });

  document.getElementById('btn-cobrar').addEventListener('click', () => {
    if (estado.total < 0.5) {
      sonidoError();
      toast('Toca el total para ingresarlo', 'error');
      return;
    }
    iniciarCobro();
  });

  function iniciarCobro() {
    estado.metodosPago = [];
    document.getElementById('monto-grande').textContent = fmt(estado.total);
    document.getElementById('metodo-monto-cabecera').textContent = fmt(estado.total);
    actualizarPagosAcumulados();
    irA('metodo');
  }

  function actualizarPagosAcumulados() {
    const cont = document.getElementById('pagos-acumulados');
    const lista = document.getElementById('pagos-lista');
    if (estado.metodosPago.length === 0) {
      cont.classList.add('oculto');
      document.getElementById('metodos-titulo-txt').textContent = '¿Cómo te paga?';
      return;
    }
    lista.innerHTML = '';
    estado.metodosPago.forEach(p => {
      const li = document.createElement('li');
      li.className = 'pago-item';
      const nombre = { yape: 'Yape', plin: 'Plin', efectivo: 'Efectivo' }[p.metodo];
      let detalle = nombre;
      if (p.nOperacion) detalle += ` · op ${p.nOperacion}`;
      li.innerHTML = `
        <span class="pago-item-metodo">${detalle}</span>
        <span class="pago-item-monto">+ ${fmt(p.monto)}</span>
      `;
      lista.appendChild(li);
    });
    const cobrado = estado.metodosPago.reduce((s, p) => s + p.monto, 0);
    const falta = estado.total - cobrado;
    document.getElementById('pagos-cobrado').textContent = fmt(cobrado);
    document.getElementById('pagos-falta').textContent = fmt(falta);
    cont.classList.remove('oculto');
    document.getElementById('metodos-titulo-txt').textContent = falta > 0
      ? `Falta cobrar ${fmt(falta)}`
      : 'Pago completo';
  }

  document.getElementById('btn-rehacer').addEventListener('click', () => {
    estado.items = [];
    estado.total = 0;
    document.getElementById('texto-venta').value = '';
    irA('dictar', { sentido: 'izquierda' });
  });

  // Botón volver desde método: si hay pagos, advertir
  document.getElementById('btn-volver-metodo').addEventListener('click', () => {
    sonidoTap();
    if (estado.metodosPago.length > 0) {
      if (!confirm('Volver descartará los pagos parciales ya registrados. ¿Continuar?')) return;
    }
    estado.metodosPago = [];
    irA('items', { sentido: 'izquierda' });
  });

  // ============================================================
  // SPEECH
  // ============================================================
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  let recognition = null;
  let escuchando = false;
  if (SpeechRecognition) {
    recognition = new SpeechRecognition();
    recognition.lang = 'es-PE';
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    recognition.onstart = () => {
      escuchando = true;
      document.getElementById('btn-dictar').classList.add('escuchando');
      document.querySelector('.dictar-texto').textContent = 'Habla ahora...';
      document.querySelector('.dictar-hint').textContent = 'Toca de nuevo para detener';
    };
    recognition.onresult = (e) => {
      const transcript = e.results[0][0].transcript;
      document.getElementById('texto-venta').value = transcript;
      tono(880, 0.1, 'sine', 0.12);
    };
    recognition.onerror = (e) => {
      sonidoError();
      if (e.error === 'no-speech') toast('No te escuché, intenta de nuevo', 'error');
      else if (e.error === 'not-allowed') toast('Permiso de micrófono denegado', 'error');
    };
    recognition.onend = () => {
      escuchando = false;
      document.getElementById('btn-dictar').classList.remove('escuchando');
      document.querySelector('.dictar-texto').textContent = 'Toca para hablar';
      document.querySelector('.dictar-hint').textContent = 'o escribe abajo';
    };
  }

  document.getElementById('btn-dictar').addEventListener('click', () => {
    sonidoTap();
    if (!recognition) { toast('Tu navegador no soporta dictado', 'error'); return; }
    if (escuchando) recognition.stop();
    else { try { recognition.start(); } catch (e) {} }
  });

  // ============================================================
  // MÉTODOS DE PAGO
  // ============================================================
  document.querySelectorAll('.metodo').forEach(btn => {
    btn.addEventListener('click', () => {
      sonidoTap();
      const metodo = btn.dataset.metodo;
      estado.metodoActual = metodo;
      const cobrado = estado.metodosPago.reduce((s, p) => s + p.monto, 0);
      const falta = estado.total - cobrado;
      // Pre-llenar monto sugerido
      document.getElementById('input-monto-pago').value = falta > 0 ? falta.toString() : '';
      document.getElementById('input-efectivo-monto').value = falta > 0 ? falta.toString() : '';
      document.getElementById('input-operacion').value = '';
      estado.nOperacion = '';
      estado.foto = null;
      document.getElementById('foto-preview').classList.add('oculto');
      document.getElementById('btn-foto-texto').textContent = 'Tomar foto';
      document.getElementById('btn-verificar').disabled = true;

      document.getElementById('verificacion-form').classList.add('oculto');
      document.getElementById('efectivo-form').classList.add('oculto');
      document.getElementById('resultado-card').classList.add('oculto');

      const titulo = { yape: 'Verificar Yape', plin: 'Verificar Plin', efectivo: 'Pago en efectivo' }[metodo];
      document.getElementById('verificar-titulo').textContent = titulo;
      document.getElementById('verificar-monto').textContent = fmt(falta);
      document.getElementById('verif-metodo-label').textContent = metodo === 'yape' ? 'Yape' : 'Plin';

      if (metodo === 'yape' || metodo === 'plin') {
        document.getElementById('verificacion-form').classList.remove('oculto');
        irA('verificar');
        setTimeout(() => document.getElementById('input-monto-pago').focus(), 500);
      } else if (metodo === 'efectivo') {
        document.getElementById('efectivo-form').classList.remove('oculto');
        irA('verificar');
        setTimeout(() => document.getElementById('input-efectivo-monto').focus(), 500);
      }
    });
  });

  // ============================================================
  // VERIFICAR YAPE/PLIN
  // ============================================================
  function validarVerificacion() {
    const monto = parseFloat(document.getElementById('input-monto-pago').value.replace(',', '.'));
    const operacion = document.getElementById('input-operacion').value.trim();
    const tieneFoto = !!estado.foto;
    const ok = !isNaN(monto) && monto >= 0.5 && operacion.length >= 4 && tieneFoto;
    document.getElementById('btn-verificar').disabled = !ok;
  }

  document.getElementById('input-monto-pago').addEventListener('input', validarVerificacion);
  document.getElementById('input-operacion').addEventListener('input', (e) => {
    estado.nOperacion = e.target.value.trim();
    validarVerificacion();
  });

  document.getElementById('input-foto').addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (!file) return;
    sonidoTap();
    const reader = new FileReader();
    reader.onload = (ev) => {
      estado.foto = ev.target.result;
      const preview = document.getElementById('foto-preview');
      preview.src = ev.target.result;
      preview.classList.remove('oculto');
      document.getElementById('btn-foto-texto').textContent = 'Cambiar foto';
      validarVerificacion();
    };
    reader.readAsDataURL(file);
  });

  // Mock: simula búsqueda en backend con timeout
  function mockBuscarPago(nOp, montoEsperado) {
    return new Promise((resolve) => {
      // Simular delay de red
      const delay = 600 + Math.random() * 400;
      setTimeout(() => {
        const ult = parseInt(nOp.slice(-1));
        if (isNaN(ult) || ult === 0) {
          resolve({ encontrado: false });
        } else if (ult >= 1 && ult <= 4) {
          resolve({
            encontrado: true,
            monto: Math.max(1, montoEsperado - (5 + ult * 2)),
            remitente: 'Cliente Demo',
            hora: 'Hace 2 min',
          });
        } else {
          resolve({
            encontrado: true,
            monto: montoEsperado,
            remitente: 'Cliente Demo',
            hora: 'Hace 1 min',
          });
        }
      }, delay);
    });
  }

  // Validación con timeout 3s
  async function validarConTimeout(nOp, monto) {
    if (!estado.online) {
      return { offline: true };
    }
    try {
      const timeoutPromise = new Promise((_, reject) => {
        setTimeout(() => reject(new Error('timeout')), 3000);
      });
      const result = await Promise.race([
        mockBuscarPago(nOp, monto),
        timeoutPromise,
      ]);
      return result;
    } catch (e) {
      return { timeout: true };
    }
  }

  document.getElementById('btn-verificar').addEventListener('click', async () => {
    const monto = parseFloat(document.getElementById('input-monto-pago').value.replace(',', '.'));
    const nOp = estado.nOperacion;
    document.getElementById('btn-verificar').disabled = true;
    document.getElementById('btn-verificar').querySelector('span').textContent = 'Verificando...';

    const r = await validarConTimeout(nOp, monto);

    document.getElementById('btn-verificar').disabled = false;
    document.getElementById('btn-verificar').querySelector('span').textContent = 'Verificar pago';

    if (r.offline) {
      // Modo offline: aceptar el monto tal cual lo digitó el vendedor
      agregarPago('yape', monto, nOp);
      mostrarResultadoOffline(monto);
    } else if (r.timeout) {
      agregarPago(estado.metodoActual, monto, nOp);
      mostrarResultadoTimeout(monto);
    } else if (!r.encontrado) {
      mostrarResultadoNoEncontrado(monto);
    } else if (r.monto < monto) {
      // El monto que dice el vendedor es mayor a lo que llegó
      // Aceptar lo que llegó realmente
      agregarPago(estado.metodoActual, r.monto, nOp);
      mostrarResultadoParcial(r);
    } else {
      agregarPago(estado.metodoActual, monto, nOp);
      mostrarResultadoOK(r, monto);
    }
  });

  function agregarPago(metodo, monto, nOperacion) {
    estado.metodosPago.push({
      metodo,
      monto,
      nOperacion: nOperacion || null,
      foto: estado.foto,
    });
  }

  function mostrarResultadoOK(r, monto) {
    const cobrado = estado.metodosPago.reduce((s, p) => s + p.monto, 0);
    const falta = estado.total - cobrado;
    if (falta > 0.01) {
      mostrarResultado('advertencia', '¡Pago parcial recibido!',
        `Recibimos ${fmt(monto)}. Faltan ${fmt(falta)} para completar.`,
        { completar: true });
    } else {
      mostrarResultado('exito', '¡Pago completo!',
        `Recibimos ${fmt(monto)} de ${r.remitente}.`,
        { boleta: true });
    }
  }

  function mostrarResultadoParcial(r) {
    const cobrado = estado.metodosPago.reduce((s, p) => s + p.monto, 0);
    const falta = estado.total - cobrado;
    if (falta > 0.01) {
      mostrarResultado('advertencia', 'Llegó menos de lo digitado',
        `Recibimos ${fmt(r.monto)} (no el monto que dijiste). Faltan ${fmt(falta)}.`,
        { completar: true });
    } else {
      mostrarResultado('exito', 'Pago completo',
        `Recibimos ${fmt(r.monto)}.`,
        { boleta: true });
    }
  }

  function mostrarResultadoOffline(monto) {
    const cobrado = estado.metodosPago.reduce((s, p) => s + p.monto, 0);
    const falta = estado.total - cobrado;
    if (falta > 0.01) {
      mostrarResultado('advertencia', 'Pago registrado (sin verificar)',
        `Sin conexión. Registramos ${fmt(monto)}. Se verificará cuando vuelva internet.`,
        { completar: true });
    } else {
      mostrarResultado('exito', 'Venta registrada',
        `Pago de ${fmt(monto)} guardado. Se verificará cuando vuelva internet.`,
        { boleta: true });
    }
  }

  function mostrarResultadoTimeout(monto) {
    const cobrado = estado.metodosPago.reduce((s, p) => s + p.monto, 0);
    const falta = estado.total - cobrado;
    const msg = `${fmt(monto)} registrado. La verificación tardó más de lo normal — se completará en segundo plano.`;
    if (falta > 0.01) {
      mostrarResultado('advertencia', 'Pago registrado', msg, { completar: true });
    } else {
      mostrarResultado('exito', 'Venta registrada', msg, { boleta: true });
    }
  }

  function mostrarResultadoNoEncontrado(monto) {
    mostrarResultado('error', 'No encontrado',
      `No hay registro de operación ${estado.nOperacion} en los últimos 10 minutos.`,
      { pendiente: true });
  }

  document.getElementById('btn-efectivo-confirmar').addEventListener('click', () => {
    const monto = parseFloat(document.getElementById('input-efectivo-monto').value.replace(',', '.'));
    if (isNaN(monto) || monto < 0.5) {
      sonidoError();
      toast('Monto inválido', 'error');
      return;
    }
    agregarPago('efectivo', monto, null);
    const cobrado = estado.metodosPago.reduce((s, p) => s + p.monto, 0);
    const falta = estado.total - cobrado;
    if (falta > 0.01) {
      mostrarResultado('advertencia', 'Efectivo registrado',
        `${fmt(monto)} en efectivo. Faltan ${fmt(falta)}.`,
        { completar: true });
    } else {
      mostrarResultado('exito', '¡Venta completa!',
        `${fmt(monto)} recibidos en efectivo.`,
        { boleta: true });
    }
  });

  function mostrarResultado(tipo, titulo, texto, botones = {}) {
    const card = document.getElementById('resultado-card');
    card.className = 'resultado-card ' + tipo;
    const svgs = {
      exito: '<polyline points="4 12 10 18 20 6"></polyline>',
      advertencia: '<line x1="12" y1="3" x2="12" y2="15"></line><circle cx="12" cy="20" r="1.5" fill="white" stroke="none"></circle>',
      error: '<line x1="6" y1="6" x2="18" y2="18"></line><line x1="18" y1="6" x2="6" y2="18"></line>',
    };
    document.getElementById('resultado-svg').innerHTML = svgs[tipo] || svgs.error;
    document.getElementById('resultado-titulo').textContent = titulo;
    document.getElementById('resultado-texto').textContent = texto;

    document.getElementById('btn-completar-pago').classList.toggle('oculto', !botones.completar);
    document.getElementById('btn-emitir-boleta').classList.toggle('oculto', !botones.boleta);
    document.getElementById('btn-finalizar').classList.toggle('oculto', !botones.boleta);
    document.getElementById('btn-pendiente').classList.toggle('oculto', !botones.pendiente);

    document.getElementById('verificacion-form').classList.add('oculto');
    document.getElementById('efectivo-form').classList.add('oculto');
    card.classList.remove('oculto');

    if (tipo === 'exito') sonidoExito();
    else if (tipo === 'advertencia') sonidoAdvertencia();
    else sonidoError();
  }

  // Completar pago: volver a elegir método
  document.getElementById('btn-completar-pago').addEventListener('click', () => {
    sonidoTap();
    irA('metodo', { sentido: 'izquierda' });
  });

  // ============================================================
  // BOLETA
  // ============================================================
  document.getElementById('btn-emitir-boleta').addEventListener('click', () => {
    sonidoTap();
    estado.cliente = { tipoDoc: 'ninguno', numero: '', nombre: '' };
    document.querySelectorAll('.tipo-doc-btn').forEach(b => b.classList.remove('activo'));
    document.querySelector('.tipo-doc-btn[data-tipo="ninguno"]').classList.add('activo');
    document.getElementById('doc-input-grupo').classList.add('oculto');
    document.getElementById('input-doc').value = '';
    document.getElementById('input-cliente-nombre').value = '';
    document.getElementById('cliente-form').classList.remove('oculto');
    document.getElementById('boleta-vista').classList.add('oculto');
    irA('boleta');
  });

  document.querySelectorAll('.tipo-doc-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      sonidoTap();
      document.querySelectorAll('.tipo-doc-btn').forEach(b => b.classList.remove('activo'));
      btn.classList.add('activo');
      const tipo = btn.dataset.tipo;
      estado.cliente.tipoDoc = tipo;
      const grupo = document.getElementById('doc-input-grupo');
      const input = document.getElementById('input-doc');
      if (tipo === 'ninguno') {
        grupo.classList.add('oculto');
      } else {
        grupo.classList.remove('oculto');
        if (tipo === 'dni') { input.placeholder = 'DNI (8 dígitos)'; input.maxLength = 8; }
        else if (tipo === 'ruc') { input.placeholder = 'RUC (11 dígitos)'; input.maxLength = 11; }
        input.value = '';
        setTimeout(() => input.focus(), 100);
      }
    });
  });

  document.getElementById('input-doc').addEventListener('input', (e) => {
    e.target.value = e.target.value.replace(/\D/g, '');
    estado.cliente.numero = e.target.value;
  });

  document.getElementById('input-cliente-nombre').addEventListener('input', (e) => {
    estado.cliente.nombre = e.target.value;
  });

  document.getElementById('btn-generar-boleta').addEventListener('click', () => {
    sonidoTap();
    if (estado.cliente.tipoDoc === 'dni' && estado.cliente.numero.length !== 8) {
      sonidoError(); toast('DNI debe tener 8 dígitos', 'error'); return;
    }
    if (estado.cliente.tipoDoc === 'ruc' && estado.cliente.numero.length !== 11) {
      sonidoError(); toast('RUC debe tener 11 dígitos', 'error'); return;
    }
    generarBoleta();
  });

  function generarBoleta() {
    const v = estado.vendedor;
    const empresa = estado.empresa;
    const local = estado.localActual;
    const tipoComprobante = estado.cliente.tipoDoc === 'ruc' ? 'F' : 'B';
    const serie = tipoComprobante === 'F' ? v.serieF : v.serieB;
    if (!estado.correlativos[serie]) estado.correlativos[serie] = 0;
    estado.correlativos[serie]++;
    const correlativo = estado.correlativos[serie];
    guardarEstado();

    const ahora = new Date();
    const dd = String(ahora.getDate()).padStart(2, '0');
    const mm = String(ahora.getMonth() + 1).padStart(2, '0');
    const yyyy = ahora.getFullYear();
    const hh = String(ahora.getHours()).padStart(2, '0');
    const mi = String(ahora.getMinutes()).padStart(2, '0');

    const totalNum = estado.total;
    const aplicaIGV = empresa.aplicaIGV;
    let subtotal, igv;
    if (aplicaIGV) {
      subtotal = +(totalNum / 1.18).toFixed(2);
      igv = +(totalNum - subtotal).toFixed(2);
    } else {
      subtotal = totalNum;
      igv = 0;
    }

    const tipoLabel = tipoComprobante === 'F' ? 'FACTURA ELECTRÓNICA' : 'BOLETA DE VENTA ELECTRÓNICA';
    const correlativoStr = String(correlativo).padStart(8, '0');

    // Items con precio unitario calculado
    const itemsHtml = estado.items.map(item => {
      const punit = item.precioUnit ? fmt2(item.precioUnit) : '-';
      const subtotalItem = item.precioUnit ? fmt2(item.cantidad * item.precioUnit) : '-';
      return `
        <tr>
          <td>${item.cantidad}</td>
          <td>${escapeHtml(item.nombre)}</td>
          <td>${punit}</td>
          <td>${subtotalItem}</td>
        </tr>
      `;
    }).join('');

    const clienteHtml = estado.cliente.tipoDoc !== 'ninguno' ? `
      <div class="b-cliente">
        <div class="b-cliente-linea"><strong>${estado.cliente.tipoDoc.toUpperCase()}:</strong> ${estado.cliente.numero}</div>
        ${estado.cliente.nombre ? `<div class="b-cliente-linea"><strong>Cliente:</strong> ${escapeHtml(estado.cliente.nombre)}</div>` : ''}
      </div>
    ` : '';

    const pendienteHtml = !empresa.tieneCDT
      ? `<div class="b-pendiente">PENDIENTE DE EMISIÓN</div>` : '';

    // Pagos
    let pagoHtml;
    if (estado.metodosPago.length === 1) {
      const p = estado.metodosPago[0];
      const nombre = { yape: 'YAPE', plin: 'PLIN', efectivo: 'EFECTIVO' }[p.metodo];
      pagoHtml = `<div class="b-pago">Pago: ${nombre}${p.nOperacion ? ' op ' + p.nOperacion : ''} · ${fmt(p.monto)}</div>`;
    } else {
      const lineasPagos = estado.metodosPago.map(p => {
        const nombre = { yape: 'YAPE', plin: 'PLIN', efectivo: 'EFECTIVO' }[p.metodo];
        return `<div>${nombre}${p.nOperacion ? ' op ' + p.nOperacion : ''}: ${fmt(p.monto)}</div>`;
      }).join('');
      pagoHtml = `
        <div class="b-pago b-pago-multi">
          <div class="b-pago-multi-titulo">Pagos:</div>
          ${lineasPagos}
        </div>
      `;
    }

    const totalesHtml = aplicaIGV
      ? `<div class="b-tot-linea"><span>Op. gravada:</span><span>S/ ${subtotal.toFixed(2)}</span></div>
         <div class="b-tot-linea"><span>IGV 18%:</span><span>S/ ${igv.toFixed(2)}</span></div>
         <div class="b-tot-linea gran-total"><span>TOTAL:</span><span>S/ ${totalNum.toFixed(2)}</span></div>`
      : `<div class="b-tot-linea"><span>Op. exonerada (Amazonía):</span><span>S/ ${subtotal.toFixed(2)}</span></div>
         <div class="b-tot-linea gran-total"><span>TOTAL:</span><span>S/ ${totalNum.toFixed(2)}</span></div>`;

    const html = `
      <div class="b-header">
        <div class="b-empresa">${escapeHtml(empresa.nombre)}</div>
        <div class="b-empresa-info">RUC ${estado.ruc}</div>
        <div class="b-empresa-info">${escapeHtml(local.direccion)}</div>
        <div class="b-empresa-info">${escapeHtml(empresa.ciudad)}</div>
      </div>
      <div class="b-divider"></div>
      <div class="b-tipo">${tipoLabel}</div>
      <div class="b-numero">${serie} - ${correlativoStr}</div>
      ${pendienteHtml}
      <div class="b-divider"></div>
      <div class="b-meta"><strong>Fecha:</strong> ${dd}/${mm}/${yyyy} ${hh}:${mi}</div>
      <div class="b-meta"><strong>Vendedor:</strong> ${escapeHtml(v.nombre)}</div>
      ${clienteHtml}
      <div class="b-divider"></div>
      <table class="b-items-tabla">
        <thead>
          <tr>
            <th>Cant</th>
            <th>Descripción</th>
            <th>P.Unit</th>
            <th>Subtotal</th>
          </tr>
        </thead>
        <tbody>${itemsHtml}</tbody>
      </table>
      <div class="b-divider"></div>
      <div class="b-totales">${totalesHtml}</div>
      ${pagoHtml}
      <div class="b-qr-wrap"><div class="b-qr"></div></div>
      <div class="b-footer">
        Representación impresa<br>
        Consulta este comprobante en<br>
        facturalo.pro/v/${serie}-${correlativoStr}
      </div>
    `;
    document.getElementById('boleta-papel').innerHTML = html;
    document.getElementById('cliente-form').classList.add('oculto');
    document.getElementById('boleta-vista').classList.remove('oculto');
    sonidoExito();
  }

  document.getElementById('btn-compartir').addEventListener('click', async () => {
    sonidoTap();
    const texto = generarTextoBoleta();
    if (navigator.share) {
      try { await navigator.share({ title: 'Comprobante de venta', text: texto }); } catch (e) {}
    } else {
      try {
        await navigator.clipboard.writeText(texto);
        toast('Copiado al portapapeles', 'exito');
      } catch (e) { toast('No se pudo compartir', 'error'); }
    }
  });

  function generarTextoBoleta() {
    const empresa = estado.empresa;
    const local = estado.localActual;
    const v = estado.vendedor;
    const ahora = new Date();
    const fechaStr = ahora.toLocaleDateString('es-PE') + ' ' + ahora.toLocaleTimeString('es-PE', { hour: '2-digit', minute: '2-digit' });
    const tipo = estado.cliente.tipoDoc === 'ruc' ? 'F' : 'B';
    const serie = tipo === 'F' ? v.serieF : v.serieB;
    const correlativo = String(estado.correlativos[serie]).padStart(8, '0');

    let txt = `*${empresa.nombre}*\nRUC ${estado.ruc}\n${local.direccion}\n\n`;
    txt += `*${estado.cliente.tipoDoc === 'ruc' ? 'FACTURA' : 'BOLETA'} DE VENTA*\n${serie} - ${correlativo}\n`;
    if (!empresa.tieneCDT) txt += `_PENDIENTE DE EMISIÓN_\n`;
    txt += `Fecha: ${fechaStr}\nVendedor: ${v.nombre}\n\n`;
    if (estado.cliente.tipoDoc !== 'ninguno') {
      txt += `${estado.cliente.tipoDoc.toUpperCase()}: ${estado.cliente.numero}\n`;
      if (estado.cliente.nombre) txt += `Cliente: ${estado.cliente.nombre}\n`;
      txt += '\n';
    }
    estado.items.forEach(item => {
      const sub = item.precioUnit ? ` = ${fmt(item.cantidad * item.precioUnit)}` : '';
      const punit = item.precioUnit ? ` (S/${fmt2(item.precioUnit)} c/u)` : '';
      txt += `${item.cantidad}× ${item.nombre}${punit}${sub}\n`;
    });
    txt += `\n*TOTAL: ${fmt(estado.total)}*\n`;
    if (estado.metodosPago.length === 1) {
      const p = estado.metodosPago[0];
      const nombre = { yape: 'Yape', plin: 'Plin', efectivo: 'Efectivo' }[p.metodo];
      txt += `Pago: ${nombre}${p.nOperacion ? ' op ' + p.nOperacion : ''}\n`;
    } else {
      txt += `Pagos:\n`;
      estado.metodosPago.forEach(p => {
        const nombre = { yape: 'Yape', plin: 'Plin', efectivo: 'Efectivo' }[p.metodo];
        txt += `  ${nombre}${p.nOperacion ? ' op ' + p.nOperacion : ''}: ${fmt(p.monto)}\n`;
      });
    }
    txt += `\nVerifica en: facturalo.pro/v/${serie}-${correlativo}\n\n_Gracias por tu compra_`;
    return txt;
  }

  document.getElementById('btn-imprimir').addEventListener('click', () => {
    sonidoTap();
    window.print();
  });

  function reiniciarFlujo() {
    estado.items = [];
    estado.total = 0;
    estado.metodosPago = [];
    estado.metodoActual = null;
    estado.nOperacion = '';
    estado.foto = null;
    estado.cliente = { tipoDoc: 'ninguno', numero: '', nombre: '' };
    document.getElementById('texto-venta').value = '';
    document.getElementById('resultado-card').classList.add('oculto');
    document.getElementById('cliente-form').classList.remove('oculto');
    document.getElementById('boleta-vista').classList.add('oculto');
    actualizarSugerencias();
  }

  function guardarVenta() {
    const venta = {
      id: 'v_' + Date.now(),
      timestamp: new Date().toISOString(),
      vendedor: estado.vendedor.nombre,
      negocio: estado.empresa.nombre,
      items: [...estado.items],
      total: estado.total,
      metodos: [...estado.metodosPago],
      cliente: estado.cliente.tipoDoc !== 'ninguno' ? { ...estado.cliente } : null,
      online: estado.online,
    };
    estado.historial.push(venta);
    // Actualizar catálogo
    estado.items.forEach(item => {
      const existe = estado.catalogo.find(c => c.nombre.toLowerCase() === item.nombre.toLowerCase());
      if (existe) {
        existe.veces++;
        existe.ultimaVez = new Date().toISOString();
        if (item.precioUnit && !item.calculado) {
          existe.precioUnit = item.precioUnit; // actualizar precio
        }
      } else {
        estado.catalogo.push({
          nombre: item.nombre,
          alias: item.nombre.toLowerCase().split(' ')[0],
          precioUnit: (item.precioUnit && !item.calculado) ? item.precioUnit : null,
          veces: 1,
          ultimaVez: new Date().toISOString(),
        });
      }
    });
    guardarEstado();
  }

  document.getElementById('btn-finalizar').addEventListener('click', () => {
    guardarVenta();
    reiniciarFlujo();
    irA('dictar', { sentido: 'izquierda' });
    setTimeout(() => toast('Venta registrada', 'exito'), 200);
  });

  document.getElementById('btn-boleta-finalizar').addEventListener('click', () => {
    guardarVenta();
    reiniciarFlujo();
    irA('dictar', { sentido: 'izquierda' });
    setTimeout(() => toast('Venta registrada', 'exito'), 200);
  });

  document.getElementById('btn-pendiente').addEventListener('click', () => {
    guardarVenta();
    reiniciarFlujo();
    irA('dictar', { sentido: 'izquierda' });
    setTimeout(() => toast('Venta pendiente', ''), 200);
  });

  // ============================================================
  // VOLVER
  // ============================================================
  document.querySelectorAll('[data-ir]').forEach(btn => {
    btn.addEventListener('click', () => {
      sonidoTap();
      irA(btn.dataset.ir, { sentido: 'izquierda' });
    });
  });

  // ============================================================
  // MENÚ
  // ============================================================
  function actualizarMenu() {
    document.getElementById('catalogo-count').textContent = estado.catalogo.length;
    document.getElementById('historial-count').textContent = estado.historial.length;
    if (estado.vendedor) {
      document.getElementById('menu-info-vendedor').textContent = estado.vendedor.nombre + ' · ' + estado.empresa.nombre;
      document.getElementById('menu-info-serie').textContent = 'SERIE ' + estado.vendedor.serieB + ' / ' + estado.vendedor.serieF;
      document.getElementById('menu-info').classList.remove('oculto');
    } else {
      document.getElementById('menu-info').classList.add('oculto');
    }
    document.getElementById('sonido-icono').textContent = estado.sonidoActivo ? '🔊' : '🔇';
    document.getElementById('sonido-estado').textContent = estado.sonidoActivo ? 'ON' : 'OFF';
  }

  document.getElementById('btn-menu').addEventListener('click', () => {
    sonidoTap();
    actualizarMenu();
    document.getElementById('menu-overlay').classList.remove('oculto');
  });

  document.getElementById('menu-cerrar').addEventListener('click', () => {
    document.getElementById('menu-overlay').classList.add('oculto');
  });

  document.getElementById('menu-overlay').addEventListener('click', (e) => {
    if (e.target.id === 'menu-overlay') {
      document.getElementById('menu-overlay').classList.add('oculto');
    }
  });

  document.getElementById('menu-historial').addEventListener('click', () => {
    document.getElementById('menu-overlay').classList.add('oculto');
    if (estado.historial.length === 0) { toast('No hay ventas hoy'); return; }
    const total = estado.historial.reduce((s, v) => s + v.total, 0);
    toast(`${estado.historial.length} ventas · Total: ${fmt(total)}`, 'exito');
  });

  document.getElementById('menu-catalogo').addEventListener('click', () => {
    document.getElementById('menu-overlay').classList.add('oculto');
    if (estado.catalogo.length === 0) { toast('Catálogo vacío'); return; }
    const top3 = [...estado.catalogo].sort((a, b) => b.veces - a.veces).slice(0, 3);
    toast('Top: ' + top3.map(p => `${p.nombre} (${p.veces}×)`).join(', '));
  });

  document.getElementById('menu-sonido').addEventListener('click', () => {
    estado.sonidoActivo = !estado.sonidoActivo;
    actualizarMenu();
    guardarEstado();
    if (estado.sonidoActivo) sonidoExito();
  });

  document.getElementById('menu-cambiar').addEventListener('click', () => {
    document.getElementById('menu-overlay').classList.add('oculto');
    estado.vendedor = null;
    estado.pin = '';
    actualizarPinDisplay();
    irA('login', { sentido: 'izquierda' });
  });

  // ============================================================
  // PWA
  // ============================================================
  let deferredPrompt = null;
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
    setTimeout(() => document.getElementById('banner-instalar').classList.remove('oculto'), 3000);
  });

  document.getElementById('btn-instalar').addEventListener('click', async () => {
    if (!deferredPrompt) return;
    deferredPrompt.prompt();
    const { outcome } = await deferredPrompt.userChoice;
    if (outcome === 'accepted') { sonidoExito(); toast('Instalado', 'exito'); }
    deferredPrompt = null;
    document.getElementById('banner-instalar').classList.add('oculto');
  });

  document.getElementById('btn-banner-cerrar').addEventListener('click', () => {
    document.getElementById('banner-instalar').classList.add('oculto');
  });

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('sw.js').catch(() => {});
    });
  }

  // ============================================================
  // INIT
  // ============================================================
  cargarEstado();
  actualizarConexion();
  document.body.addEventListener('click', function activarAudio() {
    getAudioCtx();
    document.body.removeEventListener('click', activarAudio);
  }, { once: true });

})();