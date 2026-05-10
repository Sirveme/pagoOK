// ============================================================
// pagoOK Caja v2 - Lógica
// ============================================================
// - 5 sub-pantallas con transición tipo carrusel
// - Audio sintetizado (sin marcas registradas)
// - Parser corregido: NO distribuye precios automáticamente
// - localStorage para catálogo, historial, sonido on/off
// ============================================================

(function() {
  'use strict';

  // ============================================================
  // ESTADO GLOBAL
  // ============================================================
  const estado = {
    pantalla: 'login',
    historialPantallas: [],
    ruc: '',
    pin: '',
    vendedor: null,
    items: [],
    total: 0,
    metodoPago: null,
    nOperacion: '',
    foto: null,
    catalogo: [],
    historial: [],
    sonidoActivo: true,
  };

  // Mock de vendedores válidos
  const VENDEDORES_DEMO = [
    { ruc: '20615446565', pin: '1234', nombre: 'Carlos', negocio: 'Pollería Bolognesi' },
    { ruc: '20615446565', pin: '5678', nombre: 'María', negocio: 'Pollería Bolognesi' },
    { ruc: '99999999999', pin: '0000', nombre: 'Demo', negocio: 'Negocio Demo' },
  ];

  // ============================================================
  // SISTEMA DE AUDIO (Web Audio API, todo sintetizado)
  // ============================================================
  let audioCtx = null;

  function getAudioCtx() {
    if (!audioCtx) {
      try {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      } catch (e) {
        console.warn('AudioContext no disponible', e);
      }
    }
    return audioCtx;
  }

  function tocarTono(frecuencia, duracion, tipo = 'sine', volumen = 0.15, ataque = 0.005, decay = 0) {
    if (!estado.sonidoActivo) return;
    const ctx = getAudioCtx();
    if (!ctx) return;

    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);

    osc.type = tipo;
    osc.frequency.setValueAtTime(frecuencia, ctx.currentTime);

    gain.gain.setValueAtTime(0, ctx.currentTime);
    gain.gain.linearRampToValueAtTime(volumen, ctx.currentTime + ataque);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + duracion);

    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + duracion + 0.05);
  }

  // Sonido suave de transición entre pantallas (swoosh)
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

  // Sonido de éxito festivo: 3 notas ascendentes (tipo cha-ching pero único)
  function sonidoExito() {
    if (!estado.sonidoActivo) return;
    setTimeout(() => tocarTono(523.25, 0.15, 'triangle', 0.18), 0);    // C5
    setTimeout(() => tocarTono(659.25, 0.15, 'triangle', 0.18), 100);  // E5
    setTimeout(() => tocarTono(783.99, 0.30, 'triangle', 0.20), 200);  // G5
    // Un toque de campana al final
    setTimeout(() => tocarTono(1046.50, 0.4, 'sine', 0.10, 0.005, 0.2), 240); // C6
  }

  // Sonido de advertencia: 2 notas descendentes
  function sonidoAdvertencia() {
    if (!estado.sonidoActivo) return;
    setTimeout(() => tocarTono(523.25, 0.20, 'sine', 0.18), 0);     // C5
    setTimeout(() => tocarTono(415.30, 0.30, 'sine', 0.18), 200);   // G#4
  }

  // Sonido de error: tono bajo + buzz
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

  // Sonido pequeño tipo "tap" para botones del PIN
  function sonidoTap() {
    if (!estado.sonidoActivo) return;
    tocarTono(1500, 0.04, 'sine', 0.08);
  }

  // ============================================================
  // PERSISTENCIA
  // ============================================================
  function cargarEstado() {
    try {
      const saved = localStorage.getItem('pagook_caja_v2');
      if (saved) {
        const data = JSON.parse(saved);
        estado.catalogo = data.catalogo || [];
        estado.historial = data.historial || [];
        estado.sonidoActivo = data.sonidoActivo !== false;
      }
    } catch (e) {
      console.warn('No se pudo cargar estado', e);
    }
  }

  function guardarEstado() {
    try {
      localStorage.setItem('pagook_caja_v2', JSON.stringify({
        catalogo: estado.catalogo,
        historial: estado.historial,
        sonidoActivo: estado.sonidoActivo,
      }));
    } catch (e) {
      console.warn('No se pudo guardar estado', e);
    }
  }

  // ============================================================
  // NAVEGACIÓN ENTRE PANTALLAS
  // ============================================================
  // Pantallas mayores donde se reproduce sonido de transición
  const PANTALLAS_CON_SWOOSH = ['login', 'dictar', 'verificar'];

  function irA(nombre, opciones = {}) {
    const actual = document.querySelector('.pantalla.activa');
    const proxima = document.getElementById('p-' + nombre);

    if (!proxima || actual === proxima) return;

    const { sentido = 'derecha', conSwoosh = null } = opciones;

    // Decidir si hace swoosh
    const haceSonido = conSwoosh !== null
      ? conSwoosh
      : (PANTALLAS_CON_SWOOSH.includes(nombre) || PANTALLAS_CON_SWOOSH.includes(estado.pantalla));

    if (haceSonido) sonidoSwoosh();

    // Animación
    if (actual) {
      if (sentido === 'derecha') {
        actual.classList.add('saliente-izquierda');
      } else {
        actual.classList.add('entrante-izquierda');
      }
      actual.classList.remove('activa');
    }

    proxima.classList.remove('saliente-izquierda', 'entrante-izquierda');

    if (sentido === 'derecha') {
      // Próxima viene desde la derecha (default)
    } else {
      proxima.classList.add('entrante-izquierda');
    }

    // Forzar reflow
    void proxima.offsetWidth;

    proxima.classList.remove('entrante-izquierda');
    proxima.classList.add('activa');

    estado.pantalla = nombre;

    // Limpiar la pantalla saliente después de la animación
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
    toastTimer = setTimeout(() => {
      el.classList.remove('visible');
    }, 2800);
  }

  // ============================================================
  // P1: LOGIN
  // ============================================================
  function actualizarPinDisplay() {
    const dots = document.querySelectorAll('.pin-dot');
    dots.forEach((dot, i) => {
      if (i < estado.pin.length) {
        dot.classList.add('lleno');
      } else {
        dot.classList.remove('lleno');
      }
    });
  }

  function intentarLogin() {
    const ruc = document.getElementById('input-ruc').value.trim();
    const pin = estado.pin;
    const errorEl = document.getElementById('login-error');

    if (ruc.length !== 11) {
      errorEl.textContent = 'El RUC debe tener 11 dígitos';
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

    const v = VENDEDORES_DEMO.find(x => x.ruc === ruc && x.pin === pin);
    if (!v) {
      errorEl.textContent = 'RUC o PIN incorrecto';
      errorEl.classList.remove('oculto');
      estado.pin = '';
      actualizarPinDisplay();
      sonidoError();
      return;
    }

    errorEl.classList.add('oculto');
    estado.ruc = ruc;
    estado.vendedor = v;

    document.getElementById('vendedor-nombre').textContent = v.nombre;
    document.getElementById('negocio-nombre').textContent = v.negocio;

    estado.pin = '';
    actualizarPinDisplay();

    irA('dictar');
    setTimeout(() => toast('Bienvenido ' + v.nombre, 'exito'), 200);
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
          if (estado.pin.length === 4) {
            setTimeout(intentarLogin, 220);
          }
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
  // P2: PARSER DE VENTA (corregido — NO distribuye precios)
  // ============================================================

  function parsearVenta(texto) {
    texto = texto.trim();
    if (!texto) return { items: [], total: 0, error: 'Escribe o dicta la venta' };

    const original = texto;
    let total = 0;

    // Detectar monto total con varios patrones
    // Patrones: "S/ 60", "60 soles", "total 60", "son 60", "60.50", o solo número al final
    const patronTotal = /(?:total|s\/\s*|son\s+|=\s*)?(\d+(?:[.,]\d{1,2})?)\s*(?:soles?|s\/)?\s*$/i;
    const matchTotal = texto.match(patronTotal);

    if (matchTotal) {
      total = parseFloat(matchTotal[1].replace(',', '.'));
      // Eliminar el monto y palabras conectoras
      texto = texto.substring(0, matchTotal.index).trim();
      texto = texto.replace(/[,;]$/, '').trim();
      texto = texto.replace(/(total|son|=)\s*$/i, '').trim();
    }

    if (total < 0.5) {
      return {
        items: [],
        total: 0,
        error: 'No detecté el monto. Termina con el total: "...60 soles"',
      };
    }

    // Lower case para procesamiento de items
    const textoItems = texto.toLowerCase();

    // Separar items por coma o " y "
    const segmentos = textoItems
      .split(/,|\sy\s/)
      .map(s => s.trim())
      .filter(s => s.length > 0);

    if (segmentos.length === 0) {
      // Sin items detectables, registrar como venta única
      return {
        items: [{ cantidad: 1, nombre: 'Venta', subtotal: null }],
        total: total,
      };
    }

    const numPalabras = {
      'un': 1, 'una': 1, 'uno': 1,
      'dos': 2, 'tres': 3, 'cuatro': 4, 'cinco': 5,
      'seis': 6, 'siete': 7, 'ocho': 8, 'nueve': 9, 'diez': 10,
      'once': 11, 'doce': 12, 'trece': 13, 'catorce': 14, 'quince': 15,
      'media': 0.5, 'medio': 0.5,
    };

    const items = segmentos.map(seg => {
      // Patrón: "1 X" o "1.5 X"
      const matchDigito = seg.match(/^(\d+(?:[.,]\d+)?)\s+(.+)$/);
      if (matchDigito) {
        return {
          cantidad: parseFloat(matchDigito[1].replace(',', '.')),
          nombre: matchDigito[2].trim(),
          subtotal: null,
        };
      }

      // Palabra-número al inicio
      const palabras = seg.split(/\s+/);
      if (palabras.length > 1 && numPalabras[palabras[0]] !== undefined) {
        return {
          cantidad: numPalabras[palabras[0]],
          nombre: palabras.slice(1).join(' ').trim(),
          subtotal: null,
        };
      }

      return { cantidad: 1, nombre: seg, subtotal: null };
    });

    // Limpiar nombres
    items.forEach(item => {
      item.nombre = item.nombre.replace(/^(de\s+|y\s+)/, '').trim();
      // Capitalizar primera letra
      if (item.nombre.length > 0) {
        item.nombre = item.nombre.charAt(0).toUpperCase() + item.nombre.slice(1);
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

    document.getElementById('items-total-monto').textContent = 'S/ ' + estado.total.toFixed(estado.total % 1 === 0 ? 0 : 2);

    lista.querySelectorAll('[data-accion]').forEach(btn => {
      btn.addEventListener('click', () => {
        sonidoTap();
        const idx = parseInt(btn.dataset.idx);
        const accion = btn.dataset.accion;
        const item = estado.items[idx];
        if (!item) return;

        if (accion === 'mas') {
          item.cantidad = (item.cantidad || 0) + 1;
        } else if (accion === 'menos') {
          if (item.cantidad > 1) {
            item.cantidad--;
          }
        } else if (accion === 'eliminar') {
          estado.items.splice(idx, 1);
        }
        renderItems();
      });
    });
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // Botón procesar
  document.getElementById('btn-procesar').addEventListener('click', () => {
    const texto = document.getElementById('texto-venta').value.trim();
    if (!texto) {
      sonidoError();
      toast('Escribe o dicta la venta primero', 'error');
      return;
    }

    const parsed = parsearVenta(texto);

    if (parsed.error) {
      sonidoError();
      toast(parsed.error, 'error');
      return;
    }

    if (parsed.items.length === 0 || parsed.total < 0.5) {
      sonidoError();
      toast('No pude entender la venta', 'error');
      return;
    }

    estado.items = parsed.items;
    estado.total = parsed.total;
    renderItems();

    irA('items');
  });

  // Botón cobrar
  document.getElementById('btn-cobrar').addEventListener('click', () => {
    if (estado.total < 0.5) {
      sonidoError();
      toast('El total debe ser mayor a 0', 'error');
      return;
    }

    const formatTotal = 'S/ ' + estado.total.toFixed(estado.total % 1 === 0 ? 0 : 2);
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

  // Botón rehacer
  document.getElementById('btn-rehacer').addEventListener('click', () => {
    estado.items = [];
    estado.total = 0;
    document.getElementById('texto-venta').value = '';
    irA('dictar', { sentido: 'izquierda' });
  });

  // ============================================================
  // WEB SPEECH API
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

    recognition.onresult = (event) => {
      const transcript = event.results[0][0].transcript;
      document.getElementById('texto-venta').value = transcript;
      tocarTono(880, 0.1, 'sine', 0.12);
    };

    recognition.onerror = (event) => {
      console.warn('Error speech:', event.error);
      sonidoError();
      if (event.error === 'no-speech') {
        toast('No te escuché, intenta de nuevo', 'error');
      } else if (event.error === 'not-allowed') {
        toast('Permiso de micrófono denegado', 'error');
      } else if (event.error === 'network') {
        toast('Sin conexión para reconocimiento de voz', 'error');
      }
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
    if (!recognition) {
      toast('Tu navegador no soporta dictado', 'error');
      return;
    }
    if (escuchando) {
      recognition.stop();
    } else {
      try {
        recognition.start();
      } catch (e) {
        console.warn(e);
      }
    }
  });

  // ============================================================
  // P4: ELEGIR MÉTODO
  // ============================================================
  document.querySelectorAll('.metodo').forEach(btn => {
    btn.addEventListener('click', () => {
      sonidoTap();
      const metodo = btn.dataset.metodo;
      estado.metodoPago = metodo;

      // Reset
      document.getElementById('verificacion-form').classList.add('oculto');
      document.getElementById('efectivo-form').classList.add('oculto');
      document.getElementById('resultado-card').classList.add('oculto');

      const titulo = {
        yape: 'Verificar Yape',
        plin: 'Verificar Plin',
        tarjeta: 'Pago con tarjeta',
        efectivo: 'Pago en efectivo',
      }[metodo];
      document.getElementById('verificar-titulo').textContent = titulo;

      if (metodo === 'yape' || metodo === 'plin') {
        document.getElementById('verificacion-form').classList.remove('oculto');
        irA('verificar');
        setTimeout(() => {
          document.getElementById('input-operacion').focus();
        }, 500);
      } else if (metodo === 'efectivo') {
        document.getElementById('efectivo-form').classList.remove('oculto');
        irA('verificar');
      } else if (metodo === 'tarjeta') {
        document.getElementById('verificacion-form').classList.add('oculto');
        irA('verificar');
        // Mostrar resultado directo
        setTimeout(() => {
          mostrarResultado('exito', 'Pago con tarjeta', 'Confirma manualmente que el POS procesó la venta correctamente.');
        }, 350);
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

  // Mock de búsqueda de pago en backend
  function mockBuscarPago(nOperacion, montoEsperado) {
    const ultimo = parseInt(nOperacion.slice(-1));
    if (isNaN(ultimo) || ultimo === 0) {
      return { encontrado: false };
    }
    if (ultimo >= 1 && ultimo <= 4) {
      const montoReal = Math.max(1, montoEsperado - (5 + ultimo * 2));
      return {
        encontrado: true,
        monto: montoReal,
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
        mostrarResultado(
          'error',
          'No encontrado',
          `No hay registro de la operación ${nOp} en los últimos 10 minutos.`
        );
        document.getElementById('btn-pendiente').classList.remove('oculto');
      } else if (r.monto < total) {
        const falta = total - r.monto;
        mostrarResultado(
          'advertencia',
          'Falta dinero',
          `Recibimos S/ ${r.monto.toFixed(2)} de ${r.remitente}, pero la venta es S/ ${total.toFixed(2)}. Faltan S/ ${falta.toFixed(2)}.`
        );
      } else {
        mostrarResultado(
          'exito',
          '¡Pago confirmado!',
          `Recibimos S/ ${r.monto.toFixed(2)} de ${r.remitente} ${r.hora}.`
        );
      }

      document.getElementById('btn-verificar').disabled = false;
      document.getElementById('btn-verificar').querySelector('span').textContent = 'Verificar pago';
    }, 800);
  });

  document.getElementById('btn-efectivo-confirmar').addEventListener('click', () => {
    const total = estado.total;
    mostrarResultado('exito', '¡Venta registrada!', `S/ ${total.toFixed(2)} recibidos en efectivo.`);
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

    document.getElementById('verificacion-form').classList.add('oculto');
    document.getElementById('efectivo-form').classList.add('oculto');
    card.classList.remove('oculto');

    if (tipo === 'exito') sonidoExito();
    else if (tipo === 'advertencia') sonidoAdvertencia();
    else sonidoError();
  }

  // Finalizar
  function guardarVentaEnHistorial() {
    const venta = {
      id: 'v_' + Date.now(),
      timestamp: new Date().toISOString(),
      vendedor: estado.vendedor.nombre,
      negocio: estado.vendedor.negocio,
      items: [...estado.items],
      total: estado.total,
      metodo: estado.metodoPago,
      nOperacion: estado.nOperacion || null,
      tieneFoto: !!estado.foto,
    };
    estado.historial.push(venta);

    // Catálogo (sin precios, solo nombres + frecuencia)
    estado.items.forEach(item => {
      const existe = estado.catalogo.find(c => c.nombre.toLowerCase() === item.nombre.toLowerCase());
      if (existe) {
        existe.veces++;
      } else {
        estado.catalogo.push({ nombre: item.nombre, veces: 1 });
      }
    });

    guardarEstado();
  }

  document.getElementById('btn-finalizar').addEventListener('click', () => {
    guardarVentaEnHistorial();

    estado.items = [];
    estado.total = 0;
    estado.metodoPago = null;
    estado.nOperacion = '';
    estado.foto = null;

    document.getElementById('texto-venta').value = '';
    document.getElementById('resultado-card').classList.add('oculto');

    irA('dictar', { sentido: 'izquierda' });
    setTimeout(() => toast('Venta registrada', 'exito'), 200);
  });

  document.getElementById('btn-pendiente').addEventListener('click', () => {
    guardarVentaEnHistorial();
    estado.items = [];
    estado.total = 0;
    estado.metodoPago = null;
    estado.nOperacion = '';
    estado.foto = null;
    document.getElementById('texto-venta').value = '';
    document.getElementById('resultado-card').classList.add('oculto');
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
    const sonidoIcono = document.getElementById('sonido-icono');
    const sonidoEstado = document.getElementById('sonido-estado');
    if (estado.sonidoActivo) {
      sonidoIcono.textContent = '🔊';
      sonidoEstado.textContent = 'ON';
    } else {
      sonidoIcono.textContent = '🔇';
      sonidoEstado.textContent = 'OFF';
    }
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
    if (estado.historial.length === 0) {
      toast('No hay ventas registradas hoy');
      return;
    }
    const total = estado.historial.reduce((s, v) => s + v.total, 0);
    toast(`${estado.historial.length} ventas · Total: S/ ${total.toFixed(2)}`, 'exito');
  });

  document.getElementById('menu-catalogo').addEventListener('click', () => {
    document.getElementById('menu-overlay').classList.add('oculto');
    if (estado.catalogo.length === 0) {
      toast('Aún no hay productos en el catálogo');
      return;
    }
    const top3 = [...estado.catalogo].sort((a, b) => b.veces - a.veces).slice(0, 3);
    const lista = top3.map(p => `${p.nombre} (${p.veces}×)`).join(', ');
    toast(`Top: ${lista}`);
  });

  document.getElementById('menu-sonido').addEventListener('click', () => {
    estado.sonidoActivo = !estado.sonidoActivo;
    actualizarMenu();
    guardarEstado();
    if (estado.sonidoActivo) {
      sonidoExito();
    }
  });

  document.getElementById('menu-cambiar').addEventListener('click', () => {
    document.getElementById('menu-overlay').classList.add('oculto');
    estado.vendedor = null;
    estado.ruc = '';
    estado.pin = '';
    estado.items = [];
    estado.total = 0;
    document.getElementById('input-ruc').value = '';
    document.getElementById('texto-venta').value = '';
    actualizarPinDisplay();
    irA('login', { sentido: 'izquierda' });
  });

  // ============================================================
  // PWA — Instalación
  // ============================================================
  let deferredPrompt = null;

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
    setTimeout(() => {
      document.getElementById('banner-instalar').classList.remove('oculto');
    }, 3000);
  });

  document.getElementById('btn-instalar').addEventListener('click', async () => {
    if (!deferredPrompt) return;
    deferredPrompt.prompt();
    const { outcome } = await deferredPrompt.userChoice;
    if (outcome === 'accepted') {
      sonidoExito();
      toast('pagoOK Caja instalado', 'exito');
    }
    deferredPrompt = null;
    document.getElementById('banner-instalar').classList.add('oculto');
  });

  document.getElementById('btn-banner-cerrar').addEventListener('click', () => {
    document.getElementById('banner-instalar').classList.add('oculto');
  });

  // Service Worker
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('sw.js')
        .then(reg => console.log('SW:', reg.scope))
        .catch(err => console.warn('SW falló:', err));
    });
  }

  // ============================================================
  // INIT
  // ============================================================
  cargarEstado();
  document.getElementById('input-ruc').value = '99999999999';

  // Activar AudioContext en primera interacción (requerido por navegadores)
  document.body.addEventListener('click', function activarAudio() {
    getAudioCtx();
    document.body.removeEventListener('click', activarAudio);
  }, { once: true });

})();