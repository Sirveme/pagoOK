// ============================================================
// pagoOK Caja v3 - Lógica completa
// ============================================================
// Cambios v3:
// - Parser inteligente que detecta items sin necesidad de comas
// - Total siempre editable (toca el total para cambiarlo)
// - RUC con autoselect, recordar último, mostrar nombre del negocio
// - Dropdown de locales si hay múltiples
// - Eliminado método "Tarjeta" del v1
// - Pantalla de boleta tras pago confirmado
// - Boleta con serie + correlativo, soporta "PENDIENTE DE EMISIÓN"
// - Compartir + Imprimir
// ============================================================

(function() {
  'use strict';

  // ============================================================
  // ESTADO GLOBAL
  // ============================================================
  const estado = {
    pantalla: 'login',
    ruc: '',
    pin: '',
    empresa: null,
    localActual: null,
    vendedor: null,
    items: [],
    total: 0,
    metodoPago: null,
    nOperacion: '',
    foto: null,
    cliente: { tipoDoc: 'ninguno', numero: '', nombre: '' },
    historial: [],
    catalogo: [],
    sonidoActivo: true,
    correlativos: {}, // { 'B346': 142, 'B000': 3 }
  };

  // ============================================================
  // MOCK: Datos de empresas, locales y vendedores
  // ============================================================
  const EMPRESAS_DEMO = {
    '20615446565': {
      nombre: 'Pollería Bolognesi S.A.C.',
      tieneCDT: true,
      locales: [
        {
          id: 'local_1',
          direccion: 'Av. Bolognesi 346',
          ciudad: 'Iquitos - Loreto',
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
      locales: [
        {
          id: 'local_demo',
          direccion: 'Calle Demo 100',
          ciudad: 'Iquitos - Loreto',
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
      try {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      } catch (e) {}
    }
    return audioCtx;
  }

  function tono(freq, dur, tipo = 'sine', vol = 0.15) {
    if (!estado.sonidoActivo) return;
    const ctx = getAudioCtx();
    if (!ctx) return;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.type = tipo;
    osc.frequency.setValueAtTime(freq, ctx.currentTime);
    gain.gain.setValueAtTime(0, ctx.currentTime);
    gain.gain.linearRampToValueAtTime(vol, ctx.currentTime + 0.005);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + dur);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + dur + 0.05);
  }

  function sonidoSwoosh() {
    if (!estado.sonidoActivo) return;
    const ctx = getAudioCtx();
    if (!ctx) return;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    const filter = ctx.createBiquadFilter();
    osc.connect(filter);
    filter.connect(gain);
    gain.connect(ctx.destination);
    osc.type = 'sine';
    filter.type = 'lowpass';
    filter.frequency.setValueAtTime(2000, ctx.currentTime);
    filter.frequency.exponentialRampToValueAtTime(400, ctx.currentTime + 0.18);
    osc.frequency.setValueAtTime(800, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(200, ctx.currentTime + 0.18);
    gain.gain.setValueAtTime(0, ctx.currentTime);
    gain.gain.linearRampToValueAtTime(0.08, ctx.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.18);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.2);
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
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.type = 'sawtooth';
    osc.frequency.setValueAtTime(220, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(110, ctx.currentTime + 0.4);
    gain.gain.setValueAtTime(0, ctx.currentTime);
    gain.gain.linearRampToValueAtTime(0.15, ctx.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.4);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.45);
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
      const saved = localStorage.getItem('pagook_caja_v3');
      if (saved) {
        const data = JSON.parse(saved);
        estado.catalogo = data.catalogo || [];
        estado.historial = data.historial || [];
        estado.sonidoActivo = data.sonidoActivo !== false;
        estado.correlativos = data.correlativos || {};
      }
      // Recordar último RUC
      const ultimoRuc = localStorage.getItem('pagook_ultimo_ruc');
      if (ultimoRuc) {
        document.getElementById('input-ruc').value = ultimoRuc;
        // Disparar validación
        validarRucEnVivo(ultimoRuc);
      } else {
        document.getElementById('input-ruc').value = '99999999999';
        validarRucEnVivo('99999999999');
      }
    } catch (e) {
      console.warn('No se pudo cargar estado', e);
    }
  }

  function guardarEstado() {
    try {
      localStorage.setItem('pagook_caja_v3', JSON.stringify({
        catalogo: estado.catalogo,
        historial: estado.historial,
        sonidoActivo: estado.sonidoActivo,
        correlativos: estado.correlativos,
      }));
    } catch (e) {}
  }

  function guardarRuc(ruc) {
    try {
      localStorage.setItem('pagook_ultimo_ruc', ruc);
    } catch (e) {}
  }

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
      if (sentido === 'derecha') {
        actual.classList.add('saliente-izquierda');
      }
      actual.classList.remove('activa');
    }

    proxima.classList.remove('saliente-izquierda', 'entrante-izquierda');
    if (sentido === 'izquierda') {
      proxima.classList.add('entrante-izquierda');
    }
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
  // P1: LOGIN
  // ============================================================

  // Validar RUC en vivo (al ir escribiendo)
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

    // Si hay más de un local, mostrar dropdown
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
      // Solo un local: seleccionar automáticamente
      estado.localActual = empresa.locales[0];
      localesGrupo.classList.add('oculto');
    }
  }

  document.getElementById('input-ruc').addEventListener('input', (e) => {
    const ruc = e.target.value.replace(/\D/g, '').slice(0, 11);
    e.target.value = ruc;
    validarRucEnVivo(ruc);
  });

  // Select all on focus
  document.getElementById('input-ruc').addEventListener('focus', (e) => {
    setTimeout(() => e.target.select(), 50);
  });

  // Cambiar local
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

    // Buscar vendedor con ese PIN en el local actual
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

    irA('dictar');
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
  // P2: PARSER INTELIGENTE
  // ============================================================
  // Detecta items sin necesidad de comas. Usa regex que busca
  // patrones "<número/palabra-numero> <texto hasta el siguiente número>"
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

  // Convierte texto a tokens: cada token es { tipo: 'num'|'palabra', valor }
  function tokenizar(texto) {
    const palabras = texto.toLowerCase().split(/\s+/).filter(p => p.length > 0);
    return palabras.map(p => {
      // Limpiar puntos/comas si son solo separadores
      const limpia = p.replace(/[,;.]+$/, '');
      // ¿Es número?
      if (/^\d+(?:[.,]\d+)?$/.test(limpia)) {
        return { tipo: 'num', valor: parseFloat(limpia.replace(',', '.')), texto: limpia };
      }
      // ¿Es palabra-número?
      if (NUM_PALABRAS[limpia] !== undefined) {
        return { tipo: 'num', valor: NUM_PALABRAS[limpia], texto: limpia };
      }
      // Palabra normal
      return { tipo: 'palabra', valor: limpia, texto: limpia };
    });
  }

  function parsearVenta(texto) {
    texto = texto.trim();
    if (!texto) return { items: [], total: 0, error: 'Escribe o dicta la venta' };

    let total = 0;

    // 1. Detectar total al final con patrón explícito
    const patronTotalExplicito = /(?:total|son|=|s\/\s*)\s*(\d+(?:[.,]\d{1,2})?)\s*(?:soles?|s\/)?\s*$/i;
    let matchTotal = texto.match(patronTotalExplicito);

    if (matchTotal) {
      total = parseFloat(matchTotal[1].replace(',', '.'));
      texto = texto.substring(0, matchTotal.index).trim();
    } else {
      // 2. Si no hay palabra "total/son", el último número grande puede ser el total
      // (un número >= 10 al final, o "X soles")
      const patronImplicito = /(\d+(?:[.,]\d{1,2})?)\s*(?:soles?|s\/)?\s*$/i;
      const matchImpl = texto.match(patronImplicito);
      if (matchImpl) {
        const candidato = parseFloat(matchImpl[1].replace(',', '.'));
        // Solo lo asumimos como total si es >= 10 (umbrales para negocio peruano)
        if (candidato >= 10) {
          total = candidato;
          texto = texto.substring(0, matchImpl.index).trim();
        }
      }
    }

    // Si no detecta total, igual procesar items (el vendedor podrá editarlo)
    // Ya no es bloqueante.

    // Tokenizar lo que queda
    texto = texto.replace(/[,;]/g, ' ');
    const tokens = tokenizar(texto);

    // Filtrar conectores irrelevantes
    const STOP_WORDS = ['y', 'de', 'con', 'mas', 'más', 'el', 'la', 'los', 'las', 'un', 'una'];
    const tokensFiltered = tokens.filter(t => {
      if (t.tipo === 'palabra' && STOP_WORDS.includes(t.valor)) return false;
      return true;
    });
    // Excepto: "un/una" sí es número (1)
    // Lo ya gestionamos en NUM_PALABRAS

    // Agrupar items: cada vez que aparece un token tipo 'num', empieza nuevo item
    const items = [];
    let actual = null;

    for (let i = 0; i < tokensFiltered.length; i++) {
      const t = tokensFiltered[i];
      if (t.tipo === 'num') {
        // Cerrar item anterior si tiene contenido
        if (actual && actual.nombre.length > 0) {
          items.push(actual);
        }
        // Empezar nuevo
        actual = { cantidad: t.valor, nombre: '' };
      } else {
        // Palabra → agregar al item actual
        if (!actual) {
          // Item sin cantidad explícita: cantidad = 1
          actual = { cantidad: 1, nombre: '' };
        }
        actual.nombre += (actual.nombre ? ' ' : '') + t.valor;
      }
    }

    // Cerrar el último
    if (actual && actual.nombre.length > 0) {
      items.push(actual);
    }

    // Si no se detectaron items pero había texto, registrar como genérico
    if (items.length === 0 && texto.trim().length > 0) {
      items.push({ cantidad: 1, nombre: 'Venta' });
    } else if (items.length === 0) {
      items.push({ cantidad: 1, nombre: 'Venta' });
    }

    // Capitalizar nombres
    items.forEach(item => {
      if (item.nombre.length > 0) {
        item.nombre = item.nombre.charAt(0).toUpperCase() + item.nombre.slice(1);
      } else {
        item.nombre = 'Item';
      }
    });

    return { items, total };
  }

  function renderItems() {
    const lista = document.getElementById('items-lista');
    lista.innerHTML = '';

    estado.items.forEach((item, idx) => {
      const li = document.createElement('li');
      li.className = 'item';
      li.innerHTML = `
        <div class="item-cantidad-grupo">
          <button class="item-btn-cant" data-accion="menos" data-idx="${idx}" aria-label="Menos">−</button>
          <span class="item-cantidad">${item.cantidad}</span>
          <button class="item-btn-cant" data-accion="mas" data-idx="${idx}" aria-label="Más">+</button>
        </div>
        <div class="item-info">
          <div class="item-nombre">${escapeHtml(item.nombre)}</div>
        </div>
        <button class="item-eliminar" data-accion="eliminar" data-idx="${idx}" aria-label="Eliminar">×</button>
      `;
      lista.appendChild(li);
    });

    actualizarTotalDisplay();

    lista.querySelectorAll('[data-accion]').forEach(btn => {
      btn.addEventListener('click', () => {
        sonidoTap();
        const idx = parseInt(btn.dataset.idx);
        const accion = btn.dataset.accion;
        const item = estado.items[idx];
        if (!item) return;
        if (accion === 'mas') item.cantidad++;
        else if (accion === 'menos') { if (item.cantidad > 1) item.cantidad--; }
        else if (accion === 'eliminar') estado.items.splice(idx, 1);
        renderItems();
      });
    });

    // Mostrar hint si total = 0
    document.getElementById('total-hint').classList.toggle('oculto', estado.total > 0);
  }

  function actualizarTotalDisplay() {
    const t = estado.total;
    const formatted = 'S/ ' + (t % 1 === 0 ? t.toFixed(0) : t.toFixed(2));
    document.getElementById('items-total-btn').textContent = formatted;
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // Click en total → abrir modal
  document.getElementById('items-total-btn').addEventListener('click', () => {
    sonidoTap();
    const modal = document.getElementById('modal-total');
    const input = document.getElementById('input-total-edit');
    input.value = estado.total > 0 ? estado.total.toString() : '';
    modal.classList.remove('oculto');
    setTimeout(() => {
      input.focus();
      input.select();
    }, 100);
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
    actualizarTotalDisplay();
    document.getElementById('total-hint').classList.toggle('oculto', estado.total > 0);
    document.getElementById('modal-total').classList.add('oculto');
    sonidoTap();
  });

  document.getElementById('input-total-edit').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') document.getElementById('btn-total-aceptar').click();
    if (e.key === 'Escape') document.getElementById('btn-total-cancelar').click();
  });

  // Procesar venta
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
    renderItems();

    if (parsed.total === 0) {
      // Avisar al vendedor
      toast('Falta el total — toca el monto para editarlo', '');
    }

    irA('items');
  });

  document.getElementById('btn-cobrar').addEventListener('click', () => {
    if (estado.total < 0.5) {
      sonidoError();
      toast('Toca el total para ingresarlo', 'error');
      return;
    }
    const formatTotal = 'S/ ' + (estado.total % 1 === 0 ? estado.total.toFixed(0) : estado.total.toFixed(2));
    document.getElementById('monto-grande').textContent = formatTotal;
    document.getElementById('metodo-monto-cabecera').textContent = formatTotal;
    document.getElementById('verificar-monto').textContent = formatTotal;
    document.getElementById('efectivo-monto').textContent = formatTotal;

    estado.metodoPago = null;
    estado.nOperacion = '';
    estado.foto = null;
    document.getElementById('input-operacion').value = '';
    document.getElementById('foto-preview').classList.add('oculto');
    document.getElementById('btn-foto-texto').textContent = 'Tomar foto';
    document.getElementById('btn-verificar').disabled = true;

    irA('metodo');
  });

  document.getElementById('btn-rehacer').addEventListener('click', () => {
    estado.items = [];
    estado.total = 0;
    document.getElementById('texto-venta').value = '';
    irA('dictar', { sentido: 'izquierda' });
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
  // P4: MÉTODOS
  // ============================================================
  document.querySelectorAll('.metodo').forEach(btn => {
    btn.addEventListener('click', () => {
      sonidoTap();
      const metodo = btn.dataset.metodo;
      estado.metodoPago = metodo;

      document.getElementById('verificacion-form').classList.add('oculto');
      document.getElementById('efectivo-form').classList.add('oculto');
      document.getElementById('resultado-card').classList.add('oculto');

      const titulo = { yape: 'Verificar Yape', plin: 'Verificar Plin', efectivo: 'Pago en efectivo' }[metodo];
      document.getElementById('verificar-titulo').textContent = titulo;

      if (metodo === 'yape' || metodo === 'plin') {
        document.getElementById('verificacion-form').classList.remove('oculto');
        irA('verificar');
        setTimeout(() => document.getElementById('input-operacion').focus(), 500);
      } else if (metodo === 'efectivo') {
        document.getElementById('efectivo-form').classList.remove('oculto');
        irA('verificar');
      }
    });
  });

  // ============================================================
  // P5: VERIFICAR
  // ============================================================
  function validarVerificacion() {
    const operacion = document.getElementById('input-operacion').value.trim();
    const tieneFoto = !!estado.foto;
    document.getElementById('btn-verificar').disabled = !(operacion.length >= 4 && tieneFoto);
  }

  document.getElementById('input-operacion').addEventListener('input', () => {
    estado.nOperacion = document.getElementById('input-operacion').value.trim();
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

  function mockBuscarPago(nOp, montoEsperado) {
    const ult = parseInt(nOp.slice(-1));
    if (isNaN(ult) || ult === 0) return { encontrado: false };
    if (ult >= 1 && ult <= 4) {
      return {
        encontrado: true,
        monto: Math.max(1, montoEsperado - (5 + ult * 2)),
        remitente: 'Cliente Demo',
        hora: 'Hace 2 minutos',
      };
    }
    return {
      encontrado: true,
      monto: montoEsperado,
      remitente: 'Cliente Demo',
      hora: 'Hace 1 minuto',
    };
  }

  document.getElementById('btn-verificar').addEventListener('click', () => {
    const total = estado.total;
    const nOp = estado.nOperacion;
    document.getElementById('btn-verificar').disabled = true;
    document.getElementById('btn-verificar').querySelector('span').textContent = 'Verificando...';

    setTimeout(() => {
      const r = mockBuscarPago(nOp, total);
      if (!r.encontrado) {
        mostrarResultado('error', 'No encontrado',
          `No hay registro de la operación ${nOp} en los últimos 10 minutos.`);
        document.getElementById('btn-pendiente').classList.remove('oculto');
      } else if (r.monto < total) {
        const falta = total - r.monto;
        mostrarResultado('advertencia', 'Falta dinero',
          `Recibimos S/ ${r.monto.toFixed(2)} de ${r.remitente}. Faltan S/ ${falta.toFixed(2)}.`);
      } else {
        mostrarResultado('exito', '¡Pago confirmado!',
          `Recibimos S/ ${r.monto.toFixed(2)} de ${r.remitente} ${r.hora}.`);
      }
      document.getElementById('btn-verificar').disabled = false;
      document.getElementById('btn-verificar').querySelector('span').textContent = 'Verificar pago';
    }, 800);
  });

  document.getElementById('btn-efectivo-confirmar').addEventListener('click', () => {
    mostrarResultado('exito', '¡Venta registrada!',
      `S/ ${estado.total.toFixed(2)} recibidos en efectivo.`);
  });

  function mostrarResultado(tipo, titulo, texto) {
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
    document.getElementById('btn-pendiente').classList.add('oculto');
    document.getElementById('btn-emitir-boleta').classList.toggle('oculto', tipo === 'error');
    document.getElementById('verificacion-form').classList.add('oculto');
    document.getElementById('efectivo-form').classList.add('oculto');
    card.classList.remove('oculto');

    if (tipo === 'exito') sonidoExito();
    else if (tipo === 'advertencia') sonidoAdvertencia();
    else sonidoError();
  }

  // ============================================================
  // P6: BOLETA
  // ============================================================

  // Click en "Emitir boleta"
  document.getElementById('btn-emitir-boleta').addEventListener('click', () => {
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

  // Tipo de documento
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
        if (tipo === 'dni') {
          input.placeholder = 'DNI (8 dígitos)';
          input.maxLength = 8;
        } else if (tipo === 'ruc') {
          input.placeholder = 'RUC (11 dígitos)';
          input.maxLength = 11;
        }
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

  // Generar boleta
  document.getElementById('btn-generar-boleta').addEventListener('click', () => {
    sonidoTap();
    // Validar tipo doc
    if (estado.cliente.tipoDoc === 'dni' && estado.cliente.numero.length !== 8) {
      sonidoError();
      toast('DNI debe tener 8 dígitos', 'error');
      return;
    }
    if (estado.cliente.tipoDoc === 'ruc' && estado.cliente.numero.length !== 11) {
      sonidoError();
      toast('RUC debe tener 11 dígitos', 'error');
      return;
    }

    // Generar correlativo
    const v = estado.vendedor;
    const empresa = estado.empresa;
    const local = estado.localActual;

    const tipoComprobante = estado.cliente.tipoDoc === 'ruc' ? 'F' : 'B';
    const serie = tipoComprobante === 'F' ? v.serieF : v.serieB;

    const claveCorrelativo = serie;
    if (!estado.correlativos[claveCorrelativo]) {
      estado.correlativos[claveCorrelativo] = 0;
    }
    estado.correlativos[claveCorrelativo]++;
    const correlativo = estado.correlativos[claveCorrelativo];
    guardarEstado();

    // Datos de la boleta
    const ahora = new Date();
    const dd = String(ahora.getDate()).padStart(2, '0');
    const mm = String(ahora.getMonth() + 1).padStart(2, '0');
    const yyyy = ahora.getFullYear();
    const hh = String(ahora.getHours()).padStart(2, '0');
    const mi = String(ahora.getMinutes()).padStart(2, '0');

    const totalNum = estado.total;
    const subtotal = +(totalNum / 1.18).toFixed(2);
    const igv = +(totalNum - subtotal).toFixed(2);

    const tipoLabel = tipoComprobante === 'F' ? 'FACTURA ELECTRÓNICA' : 'BOLETA DE VENTA ELECTRÓNICA';
    const correlativoStr = String(correlativo).padStart(8, '0');

    const metodoLabel = {
      yape: 'YAPE op ' + estado.nOperacion,
      plin: 'PLIN op ' + estado.nOperacion,
      efectivo: 'EFECTIVO',
    }[estado.metodoPago];

    const itemsHtml = estado.items.map(item => `
      <tr>
        <td>${item.cantidad}</td>
        <td>${escapeHtml(item.nombre)}</td>
        <td></td>
      </tr>
    `).join('');

    const clienteHtml = estado.cliente.tipoDoc !== 'ninguno' ? `
      <div class="b-cliente">
        <div class="b-cliente-linea"><strong>${estado.cliente.tipoDoc.toUpperCase()}:</strong> ${estado.cliente.numero}</div>
        ${estado.cliente.nombre ? `<div class="b-cliente-linea"><strong>Cliente:</strong> ${escapeHtml(estado.cliente.nombre)}</div>` : ''}
      </div>
    ` : '';

    const pendienteHtml = !empresa.tieneCDT
      ? `<div class="b-pendiente">PENDIENTE DE EMISIÓN</div>`
      : '';

    const html = `
      <div class="b-header">
        <div class="b-empresa">${escapeHtml(empresa.nombre)}</div>
        <div class="b-empresa-info">RUC ${estado.ruc}</div>
        <div class="b-empresa-info">${escapeHtml(local.direccion)}</div>
        <div class="b-empresa-info">${escapeHtml(local.ciudad)}</div>
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
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${itemsHtml}
        </tbody>
      </table>

      <div class="b-divider"></div>

      <div class="b-totales">
        <div class="b-tot-linea"><span>Op. gravada:</span><span>S/ ${subtotal.toFixed(2)}</span></div>
        <div class="b-tot-linea"><span>IGV 18%:</span><span>S/ ${igv.toFixed(2)}</span></div>
        <div class="b-tot-linea gran-total"><span>TOTAL:</span><span>S/ ${totalNum.toFixed(2)}</span></div>
      </div>

      <div class="b-pago">Pago: ${metodoLabel}</div>

      <div class="b-qr-wrap">
        <div class="b-qr"></div>
      </div>

      <div class="b-footer">
        Representación impresa<br>
        Consulta este comprobante en<br>
        pagook.pro/v/${serie}-${correlativoStr}
      </div>
    `;

    document.getElementById('boleta-papel').innerHTML = html;
    document.getElementById('cliente-form').classList.add('oculto');
    document.getElementById('boleta-vista').classList.remove('oculto');

    sonidoExito();
  });

  // Compartir
  document.getElementById('btn-compartir').addEventListener('click', async () => {
    sonidoTap();
    const texto = generarTextoBoleta();
    if (navigator.share) {
      try {
        await navigator.share({
          title: 'Comprobante de venta',
          text: texto,
        });
      } catch (e) {}
    } else {
      try {
        await navigator.clipboard.writeText(texto);
        toast('Boleta copiada al portapapeles', 'exito');
      } catch (e) {
        toast('No se pudo compartir', 'error');
      }
    }
  });

  function generarTextoBoleta() {
    const empresa = estado.empresa;
    const local = estado.localActual;
    const v = estado.vendedor;
    const ahora = new Date();
    const fechaStr = ahora.toLocaleDateString('es-PE') + ' ' + ahora.toLocaleTimeString('es-PE', { hour: '2-digit', minute: '2-digit' });

    let txt = `*${empresa.nombre}*\n`;
    txt += `RUC ${estado.ruc}\n`;
    txt += `${local.direccion}\n\n`;
    txt += `*${estado.cliente.tipoDoc === 'ruc' ? 'FACTURA' : 'BOLETA'} DE VENTA*\n`;
    const tipo = estado.cliente.tipoDoc === 'ruc' ? 'F' : 'B';
    const serie = tipo === 'F' ? v.serieF : v.serieB;
    const correlativo = String(estado.correlativos[serie]).padStart(8, '0');
    txt += `${serie} - ${correlativo}\n`;
    if (!empresa.tieneCDT) txt += `_PENDIENTE DE EMISIÓN_\n`;
    txt += `Fecha: ${fechaStr}\n`;
    txt += `Vendedor: ${v.nombre}\n\n`;
    if (estado.cliente.tipoDoc !== 'ninguno') {
      txt += `${estado.cliente.tipoDoc.toUpperCase()}: ${estado.cliente.numero}\n`;
      if (estado.cliente.nombre) txt += `Cliente: ${estado.cliente.nombre}\n`;
      txt += '\n';
    }
    estado.items.forEach(item => {
      txt += `${item.cantidad}× ${item.nombre}\n`;
    });
    txt += `\n*TOTAL: S/ ${estado.total.toFixed(2)}*\n`;
    const metodoLabel = { yape: 'Yape op ' + estado.nOperacion, plin: 'Plin op ' + estado.nOperacion, efectivo: 'Efectivo' }[estado.metodoPago];
    txt += `Pago: ${metodoLabel}\n\n`;
    txt += `_Gracias por tu compra_`;
    return txt;
  }

  // Imprimir
  document.getElementById('btn-imprimir').addEventListener('click', () => {
    sonidoTap();
    window.print();
  });

  // Finalizar
  function reiniciarFlujo() {
    estado.items = [];
    estado.total = 0;
    estado.metodoPago = null;
    estado.nOperacion = '';
    estado.foto = null;
    estado.cliente = { tipoDoc: 'ninguno', numero: '', nombre: '' };
    document.getElementById('texto-venta').value = '';
    document.getElementById('resultado-card').classList.add('oculto');
    document.getElementById('cliente-form').classList.remove('oculto');
    document.getElementById('boleta-vista').classList.add('oculto');
  }

  function guardarVenta() {
    const venta = {
      id: 'v_' + Date.now(),
      timestamp: new Date().toISOString(),
      vendedor: estado.vendedor.nombre,
      negocio: estado.empresa.nombre,
      items: [...estado.items],
      total: estado.total,
      metodo: estado.metodoPago,
      nOperacion: estado.nOperacion || null,
      cliente: estado.cliente.tipoDoc !== 'ninguno' ? { ...estado.cliente } : null,
    };
    estado.historial.push(venta);
    estado.items.forEach(item => {
      const e = estado.catalogo.find(c => c.nombre.toLowerCase() === item.nombre.toLowerCase());
      if (e) e.veces++;
      else estado.catalogo.push({ nombre: item.nombre, veces: 1 });
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
    setTimeout(() => toast('Venta pendiente registrada'), 200);
  });

  // ============================================================
  // BOTONES VOLVER
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
    const sIcono = document.getElementById('sonido-icono');
    const sEstado = document.getElementById('sonido-estado');
    sIcono.textContent = estado.sonidoActivo ? '🔊' : '🔇';
    sEstado.textContent = estado.sonidoActivo ? 'ON' : 'OFF';
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
    toast(`${estado.historial.length} ventas · Total: S/ ${total.toFixed(2)}`, 'exito');
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

  document.body.addEventListener('click', function activarAudio() {
    getAudioCtx();
    document.body.removeEventListener('click', activarAudio);
  }, { once: true });

})();