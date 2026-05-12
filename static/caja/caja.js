/* pagoOK Caja v8 — JS bundle generado el Tue May 12 01:04:56 UTC 2026 */

/* === 01-config.js === */
// ============================================================
// 01-config.js — Datos mock + constantes
// ============================================================

const CAJA_VERSION = 'v8.0';
const PAGOS_VENTANA_MIN = 30; // minutos de validez para pagos en memoria
const PAGOS_MAX = 50;
const TIMEOUT_VALIDACION_MS = 3000;

const EMPRESAS_DEMO = {
  '20615446565': {
    razonSocial: 'POLLERÍA BOLOGNESI S.A.C.',
    nombreComercial: 'Pollería Bolognesi',
    tieneCDT: true,
    aplicaIGV: false,
    esAmazonia: true,
    ciudad: 'Iquitos - Loreto',
    domicilioFiscal: 'Av. Iquitos 230, Iquitos',
    logoUrl: null,
    formaPago: 'CONTADO',
    yape: {
      celular: '987 654 321',
      titular: 'Pollería Bolognesi',
      qrUrl: null,
    },
    plin: {
      celular: '987 654 321',
      titular: 'Pollería Bolognesi',
      qrUrl: null,
    },
    cuentasBancarias: [
      {
        banco: 'BCP',
        tipo: 'Cuenta corriente soles',
        numero: '191-2345678-0-12',
        cci: '00219100023456781293',
        titular: 'POLLERÍA BOLOGNESI SAC',
      },
      {
        banco: 'BBVA',
        tipo: 'Cuenta corriente soles',
        numero: '0011-0234-0100123456',
        cci: '01102340010012345678',
        titular: 'POLLERÍA BOLOGNESI SAC',
      },
      {
        banco: 'Interbank',
        tipo: 'Cuenta corriente soles',
        numero: '200-3001234567',
        cci: '00320000300123456789',
        titular: 'POLLERÍA BOLOGNESI SAC',
      },
    ],
    tarjetaConfig: { voucherObligatorio: true },
    fotoConfig: { obligatoria: false },
    locales: [
      {
        id: 'local_1',
        direccion: 'Av. Bolognesi 346',
        esAnexo: true,
        serieBase: 346,
        vendedores: [
          { alias: 'vendedor1', pin: '1234', nombre: 'Carlos', serieB: 'B346', serieF: 'F346' },
          { alias: 'vendedor2', pin: '5678', nombre: 'María', serieB: 'B347', serieF: 'F347' },
        ],
      },
    ],
  },
  '99999999999': {
    razonSocial: 'NEGOCIO DEMO S.A.C.',
    nombreComercial: 'Negocio Demo',
    tieneCDT: false,
    aplicaIGV: true,
    esAmazonia: false,
    ciudad: 'Lima - Lima',
    domicilioFiscal: 'Calle Demo 100, Lima',
    logoUrl: null,
    formaPago: 'CONTADO',
    yape: { celular: '999 999 999', titular: 'Negocio Demo', qrUrl: null },
    plin: { celular: '999 999 999', titular: 'Negocio Demo', qrUrl: null },
    cuentasBancarias: [
      {
        banco: 'BCP',
        tipo: 'Cuenta corriente soles',
        numero: '100-0000000-0-00',
        cci: '00210000000000000000',
        titular: 'NEGOCIO DEMO SAC',
      },
    ],
    tarjetaConfig: { voucherObligatorio: false },
    fotoConfig: { obligatoria: false },
    locales: [
      {
        id: 'local_demo',
        direccion: 'Calle Demo 100',
        esAnexo: false,
        serieBase: null,
        vendedores: [
          { alias: 'vendedor1', pin: '0000', nombre: 'Demo', serieB: 'B000', serieF: 'F000' },
        ],
      },
    ],
  },
};

const PERSONAS_DEMO = {
  '20100123456': 'CONSTRUCTORA TEST S.A.C.',
  '20612345678': 'EMPRESA EJEMPLO PERU SAC',
  '20615446565': 'POLLERÍA BOLOGNESI S.A.C.',
  '10412345678': 'PEREZ MENDOZA JUAN CARLOS',
  '05393776': 'CESITAR RUIZ AGUILAR',
  '45678912': 'GARCIA ROJAS MARIA ELENA',
  '12345678': 'LOPEZ DIAZ CARLOS ALBERTO',
  '87654321': 'TORRES VEGA ANA SOFIA',
};

const NOMBRES_DEMO = [
  'Juan Pérez Mendoza', 'María García Rojas', 'Carlos López Díaz',
  'Ana Torres Vega', 'Luis Ramírez Flores', 'Sofía Castro Núñez',
  'Pedro Vargas Quispe', 'Rosa Mendoza Pinto', 'José Sánchez León',
  'Cesitar Ruiz Aguilar',
];

const OPERADORES = ['yape', 'plin'];

const NUM_PALABRAS = {
  'un': 1, 'una': 1, 'uno': 1,
  'dos': 2, 'tres': 3, 'cuatro': 4, 'cinco': 5,
  'seis': 6, 'siete': 7, 'ocho': 8, 'nueve': 9, 'diez': 10,
  'once': 11, 'doce': 12, 'trece': 13, 'catorce': 14, 'quince': 15,
  'dieciseis': 16, 'diecisiete': 17, 'dieciocho': 18, 'diecinueve': 19,
  'veinte': 20, 'treinta': 30,
  'media': 0.5, 'medio': 0.5,
};

const PANTALLAS_CON_SWOOSH = ['login', 'dictar', 'verificar', 'boleta'];

/* === 02-state.js === */
// ============================================================
// 02-state.js — Estado global + persistencia localStorage
// ============================================================

const estado = {
  pantalla: 'login',
  ruc: '',
  pin: '',
  empresa: null,
  localActual: null,
  vendedor: null,

  // Venta en curso
  items: [], // [{cantidad, nombre, precioUnit?, subtotal?, calculado?}]
  total: 0,
  metodosPago: [], // [{metodo, monto, nOperacion?, foto?}]
  metodoActual: null,
  nOperacion: '',
  foto: null,
  montoPagoActual: 0,
  cliente: { tipoDoc: 'ninguno', numero: '', nombre: '' },
  modalPagoMetodo: null,

  // Boleta preview (antes de grabar)
  boletaPreview: null, // { serie, correlativo, tipoComprobante, emitida }

  // Histórico
  historial: [],
  catalogo: [], // [{nombre, alias, precioUnit, veces, ultimaVez}]
  correlativos: {}, // por serie

  // Pagos en memoria (push silencioso)
  pagosEnMemoria: [], // [{nOp, monto, operador, nombre, timestamp}]

  // Preferencias
  sonidoActivo: true,
  online: navigator.onLine,

  // UI
  itemEditandoIdx: -1,
};

function persistirEstado() {
  try {
    localStorage.setItem('pagook_caja_v8', JSON.stringify({
      catalogo: estado.catalogo,
      historial: estado.historial,
      sonidoActivo: estado.sonidoActivo,
      correlativos: estado.correlativos,
      // Venta en curso (para recuperar si se cerró sin grabar)
      ventaEnCurso: estado.items.length > 0 ? {
        items: estado.items,
        total: estado.total,
        metodosPago: estado.metodosPago,
        cliente: estado.cliente,
        timestamp: new Date().toISOString(),
      } : null,
    }));
  } catch (e) {
    console.warn('[state] No se pudo persistir:', e);
  }
}

function cargarEstadoPersistido() {
  try {
    const saved = localStorage.getItem('pagook_caja_v8');
    if (saved) {
      const data = JSON.parse(saved);
      estado.catalogo = data.catalogo || [];
      estado.historial = data.historial || [];
      estado.sonidoActivo = data.sonidoActivo !== false;
      estado.correlativos = data.correlativos || {};
      // Venta en curso se carga después al hacer login
      return data;
    }
  } catch (e) {
    console.warn('[state] Error cargando:', e);
  }
  return null;
}

function guardarUltimoRuc(ruc) {
  try { localStorage.setItem('pagook_ultimo_ruc', ruc); } catch (e) {}
}

function leerUltimoRuc() {
  try { return localStorage.getItem('pagook_ultimo_ruc') || ''; } catch (e) { return ''; }
}

function limpiarVentaEnCurso() {
  estado.items = [];
  estado.total = 0;
  estado.metodosPago = [];
  estado.metodoActual = null;
  estado.nOperacion = '';
  estado.foto = null;
  estado.cliente = { tipoDoc: 'ninguno', numero: '', nombre: '' };
  estado.boletaPreview = null;
  estado.modalPagoMetodo = null;
  estado.itemEditandoIdx = -1;
}

/* === 03-audio.js === */
// ============================================================
// 03-audio.js — Web Audio API + sonidos
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

/* === 04-qrcodegen.js === */
/* 
 * QR Code generator library (compact JavaScript)
 * 
 * Based on qrcodegen by Project Nayuki
 * https://www.nayuki.io/page/qr-code-generator-library
 * Released into the public domain.
 * 
 * Compact version for inline use in pagoOK Caja.
 */

var qrcodegen = (function() {
  "use strict";
  
  function appendBits(val, len, bb) {
    if (len < 0 || len > 31 || val >>> len !== 0) throw "Value out of range";
    for (var i = len - 1; i >= 0; i--) bb.push((val >>> i) & 1);
  }
  
  function getBit(x, i) { return ((x >>> i) & 1) !== 0; }
  
  function QrCode(version, errorCorrectionLevel, dataCodewords, mask) {
    if (version < 1 || version > 40) throw "Version out of range";
    if (mask < -1 || mask > 7) throw "Mask out of range";
    this.version = version;
    this.size = version * 4 + 17;
    this.errorCorrectionLevel = errorCorrectionLevel;
    var row = [];
    for (var i = 0; i < this.size; i++) row.push(false);
    this.modules = [];
    this.isFunction = [];
    for (var i = 0; i < this.size; i++) {
      this.modules.push(row.slice());
      this.isFunction.push(row.slice());
    }
    this.drawFunctionPatterns();
    var allCodewords = this.addEccAndInterleave(dataCodewords);
    this.drawCodewords(allCodewords);
    if (mask === -1) {
      var minPenalty = 1000000000;
      for (var i = 0; i < 8; i++) {
        this.applyMask(i);
        this.drawFormatBits(i);
        var penalty = this.getPenaltyScore();
        if (penalty < minPenalty) { mask = i; minPenalty = penalty; }
        this.applyMask(i);
      }
    }
    this.mask = mask;
    this.applyMask(mask);
    this.drawFormatBits(mask);
    this.isFunction = null;
  }
  
  QrCode.prototype.getModule = function(x, y) {
    return 0 <= x && x < this.size && 0 <= y && y < this.size && this.modules[y][x];
  };
  
  QrCode.prototype.drawFunctionPatterns = function() {
    for (var i = 0; i < this.size; i++) {
      this.setFunctionModule(6, i, i % 2 === 0);
      this.setFunctionModule(i, 6, i % 2 === 0);
    }
    this.drawFinderPattern(3, 3);
    this.drawFinderPattern(this.size - 4, 3);
    this.drawFinderPattern(3, this.size - 4);
    var alignPatPos = this.getAlignmentPatternPositions();
    var numAlign = alignPatPos.length;
    for (var i = 0; i < numAlign; i++) {
      for (var j = 0; j < numAlign; j++) {
        if (!(i === 0 && j === 0) && !(i === 0 && j === numAlign - 1) && !(i === numAlign - 1 && j === 0))
          this.drawAlignmentPattern(alignPatPos[i], alignPatPos[j]);
      }
    }
    this.drawFormatBits(0);
    this.drawVersion();
  };
  
  QrCode.prototype.drawFormatBits = function(mask) {
    var data = this.errorCorrectionLevel.formatBits << 3 | mask;
    var rem = data;
    for (var i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
    var bits = (data << 10 | rem) ^ 0x5412;
    for (var i = 0; i <= 5; i++) this.setFunctionModule(8, i, getBit(bits, i));
    this.setFunctionModule(8, 7, getBit(bits, 6));
    this.setFunctionModule(8, 8, getBit(bits, 7));
    this.setFunctionModule(7, 8, getBit(bits, 8));
    for (var i = 9; i < 15; i++) this.setFunctionModule(14 - i, 8, getBit(bits, i));
    for (var i = 0; i < 8; i++) this.setFunctionModule(this.size - 1 - i, 8, getBit(bits, i));
    for (var i = 8; i < 15; i++) this.setFunctionModule(8, this.size - 15 + i, getBit(bits, i));
    this.setFunctionModule(8, this.size - 8, true);
  };
  
  QrCode.prototype.drawVersion = function() {
    if (this.version < 7) return;
    var rem = this.version;
    for (var i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1F25);
    var bits = this.version << 12 | rem;
    for (var i = 0; i < 18; i++) {
      var bit = getBit(bits, i);
      var a = this.size - 11 + i % 3;
      var b = Math.floor(i / 3);
      this.setFunctionModule(a, b, bit);
      this.setFunctionModule(b, a, bit);
    }
  };
  
  QrCode.prototype.drawFinderPattern = function(x, y) {
    for (var dy = -4; dy <= 4; dy++) {
      for (var dx = -4; dx <= 4; dx++) {
        var dist = Math.max(Math.abs(dx), Math.abs(dy));
        var xx = x + dx, yy = y + dy;
        if (0 <= xx && xx < this.size && 0 <= yy && yy < this.size)
          this.setFunctionModule(xx, yy, dist !== 2 && dist !== 4);
      }
    }
  };
  
  QrCode.prototype.drawAlignmentPattern = function(x, y) {
    for (var dy = -2; dy <= 2; dy++) {
      for (var dx = -2; dx <= 2; dx++) {
        this.setFunctionModule(x + dx, y + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
      }
    }
  };
  
  QrCode.prototype.setFunctionModule = function(x, y, isDark) {
    this.modules[y][x] = isDark;
    this.isFunction[y][x] = true;
  };
  
  QrCode.prototype.addEccAndInterleave = function(data) {
    var ver = this.version;
    var ecl = this.errorCorrectionLevel;
    if (data.length !== QrCode.getNumDataCodewords(ver, ecl)) throw "Invalid argument";
    var numBlocks = QrCode.NUM_ERROR_CORRECTION_BLOCKS[ecl.ordinal][ver];
    var blockEccLen = QrCode.ECC_CODEWORDS_PER_BLOCK[ecl.ordinal][ver];
    var rawCodewords = Math.floor(QrCode.getNumRawDataModules(ver) / 8);
    var numShortBlocks = numBlocks - rawCodewords % numBlocks;
    var shortBlockLen = Math.floor(rawCodewords / numBlocks);
    var blocks = [];
    var rs = reedSolomonComputeDivisor(blockEccLen);
    for (var i = 0, k = 0; i < numBlocks; i++) {
      var dat = data.slice(k, k + shortBlockLen - blockEccLen + (i < numShortBlocks ? 0 : 1));
      k += dat.length;
      var ecc = reedSolomonComputeRemainder(dat, rs);
      if (i < numShortBlocks) dat.push(0);
      blocks.push(dat.concat(ecc));
    }
    var result = [];
    for (var i = 0; i < blocks[0].length; i++) {
      for (var j = 0; j < blocks.length; j++) {
        if (i !== shortBlockLen - blockEccLen || j >= numShortBlocks) result.push(blocks[j][i]);
      }
    }
    return result;
  };
  
  QrCode.prototype.drawCodewords = function(data) {
    if (data.length !== Math.floor(QrCode.getNumRawDataModules(this.version) / 8)) throw "Invalid argument";
    var i = 0;
    for (var right = this.size - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5;
      for (var vert = 0; vert < this.size; vert++) {
        for (var j = 0; j < 2; j++) {
          var x = right - j;
          var upward = ((right + 1) & 2) === 0;
          var y = upward ? this.size - 1 - vert : vert;
          if (!this.isFunction[y][x] && i < data.length * 8) {
            this.modules[y][x] = getBit(data[i >>> 3], 7 - (i & 7));
            i++;
          }
        }
      }
    }
  };
  
  QrCode.prototype.applyMask = function(mask) {
    if (mask < 0 || mask > 7) throw "Mask value out of range";
    for (var y = 0; y < this.size; y++) {
      for (var x = 0; x < this.size; x++) {
        var invert;
        switch (mask) {
          case 0: invert = (x + y) % 2 === 0; break;
          case 1: invert = y % 2 === 0; break;
          case 2: invert = x % 3 === 0; break;
          case 3: invert = (x + y) % 3 === 0; break;
          case 4: invert = (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0; break;
          case 5: invert = x * y % 2 + x * y % 3 === 0; break;
          case 6: invert = (x * y % 2 + x * y % 3) % 2 === 0; break;
          case 7: invert = ((x + y) % 2 + x * y % 3) % 2 === 0; break;
        }
        if (!this.isFunction[y][x] && invert) this.modules[y][x] = !this.modules[y][x];
      }
    }
  };
  
  QrCode.prototype.getPenaltyScore = function() {
    var result = 0;
    var size = this.size;
    var modules = this.modules;
    for (var y = 0; y < size; y++) {
      var runColor = false, runX = 0;
      var runHistory = [0, 0, 0, 0, 0, 0, 0];
      for (var x = 0; x < size; x++) {
        if (modules[y][x] === runColor) {
          runX++;
          if (runX === 5) result += 3;
          else if (runX > 5) result++;
        } else {
          this._finderPenaltyAddHistory(runX, runHistory);
          if (!runColor) result += this._finderPenaltyCountPatterns(runHistory) * 40;
          runColor = modules[y][x];
          runX = 1;
        }
      }
      result += this._finderPenaltyTerminateAndCount(runColor, runX, runHistory) * 40;
    }
    for (var x = 0; x < size; x++) {
      var runColor = false, runY = 0;
      var runHistory = [0, 0, 0, 0, 0, 0, 0];
      for (var y = 0; y < size; y++) {
        if (modules[y][x] === runColor) {
          runY++;
          if (runY === 5) result += 3;
          else if (runY > 5) result++;
        } else {
          this._finderPenaltyAddHistory(runY, runHistory);
          if (!runColor) result += this._finderPenaltyCountPatterns(runHistory) * 40;
          runColor = modules[y][x];
          runY = 1;
        }
      }
      result += this._finderPenaltyTerminateAndCount(runColor, runY, runHistory) * 40;
    }
    for (var y = 0; y < size - 1; y++) {
      for (var x = 0; x < size - 1; x++) {
        var color = modules[y][x];
        if (color === modules[y][x + 1] && color === modules[y + 1][x] && color === modules[y + 1][x + 1])
          result += 3;
      }
    }
    var dark = 0;
    for (var y = 0; y < size; y++) for (var x = 0; x < size; x++) if (modules[y][x]) dark++;
    var total = size * size;
    var k = Math.ceil(Math.abs(dark * 20 - total * 10) / total) - 1;
    result += k * 10;
    return result;
  };
  
  QrCode.prototype._finderPenaltyAddHistory = function(currentRunLength, runHistory) {
    if (runHistory[0] === 0) currentRunLength += this.size;
    runHistory.pop();
    runHistory.unshift(currentRunLength);
  };
  
  QrCode.prototype._finderPenaltyCountPatterns = function(runHistory) {
    var n = runHistory[1];
    var core = n > 0 && runHistory[2] === n && runHistory[3] === n * 3 && runHistory[4] === n && runHistory[5] === n;
    return (core && runHistory[0] >= n * 4 && runHistory[6] >= n ? 1 : 0)
      + (core && runHistory[6] >= n * 4 && runHistory[0] >= n ? 1 : 0);
  };
  
  QrCode.prototype._finderPenaltyTerminateAndCount = function(currentRunColor, currentRunLength, runHistory) {
    if (currentRunColor) {
      this._finderPenaltyAddHistory(currentRunLength, runHistory);
      currentRunLength = 0;
    }
    currentRunLength += this.size;
    this._finderPenaltyAddHistory(currentRunLength, runHistory);
    return this._finderPenaltyCountPatterns(runHistory);
  };
  
  QrCode.prototype.getAlignmentPatternPositions = function() {
    if (this.version === 1) return [];
    var numAlign = Math.floor(this.version / 7) + 2;
    var step = (this.version === 32) ? 26 : Math.ceil((this.size - 13) / (numAlign * 2 - 2)) * 2;
    var result = [6];
    for (var pos = this.size - 7; result.length < numAlign; pos -= step) result.splice(1, 0, pos);
    return result;
  };
  
  QrCode.getNumRawDataModules = function(ver) {
    if (ver < 1 || ver > 40) throw "Version out of range";
    var result = (16 * ver + 128) * ver + 64;
    if (ver >= 2) {
      var numAlign = Math.floor(ver / 7) + 2;
      result -= (25 * numAlign - 10) * numAlign - 55;
      if (ver >= 7) result -= 36;
    }
    return result;
  };
  
  QrCode.getNumDataCodewords = function(ver, ecl) {
    return Math.floor(QrCode.getNumRawDataModules(ver) / 8) -
      QrCode.ECC_CODEWORDS_PER_BLOCK[ecl.ordinal][ver] *
      QrCode.NUM_ERROR_CORRECTION_BLOCKS[ecl.ordinal][ver];
  };
  
  QrCode.encodeText = function(text, ecl) {
    var segs = QrSegment.makeSegments(text);
    return QrCode.encodeSegments(segs, ecl);
  };
  
  QrCode.encodeSegments = function(segs, ecl, minVersion, maxVersion, mask, boostEcl) {
    if (minVersion === undefined) minVersion = 1;
    if (maxVersion === undefined) maxVersion = 40;
    if (mask === undefined) mask = -1;
    if (boostEcl === undefined) boostEcl = true;
    var version, dataUsedBits;
    for (version = minVersion; ; version++) {
      var dataCapacityBits = QrCode.getNumDataCodewords(version, ecl) * 8;
      var usedBits = QrSegment.getTotalBits(segs, version);
      if (usedBits <= dataCapacityBits) { dataUsedBits = usedBits; break; }
      if (version >= maxVersion) throw "Data too long";
    }
    [QrCode.Ecc.MEDIUM, QrCode.Ecc.QUARTILE, QrCode.Ecc.HIGH].forEach(function(newEcl) {
      if (boostEcl && dataUsedBits <= QrCode.getNumDataCodewords(version, newEcl) * 8) ecl = newEcl;
    });
    var bb = [];
    segs.forEach(function(seg) {
      appendBits(seg.mode.modeBits, 4, bb);
      appendBits(seg.numChars, seg.mode.numCharCountBits(version), bb);
      seg.getData().forEach(function(b) { bb.push(b); });
    });
    var dataCapacityBits = QrCode.getNumDataCodewords(version, ecl) * 8;
    appendBits(0, Math.min(4, dataCapacityBits - bb.length), bb);
    appendBits(0, (8 - bb.length % 8) % 8, bb);
    for (var padByte = 0xEC; bb.length < dataCapacityBits; padByte ^= 0xEC ^ 0x11)
      appendBits(padByte, 8, bb);
    var dataCodewords = [];
    while (dataCodewords.length * 8 < bb.length) dataCodewords.push(0);
    bb.forEach(function(b, i) { dataCodewords[i >>> 3] |= b << (7 - (i & 7)); });
    return new QrCode(version, ecl, dataCodewords, mask);
  };
  
  QrCode.Ecc = {
    LOW: { ordinal: 0, formatBits: 1 },
    MEDIUM: { ordinal: 1, formatBits: 0 },
    QUARTILE: { ordinal: 2, formatBits: 3 },
    HIGH: { ordinal: 3, formatBits: 2 },
  };
  
  QrCode.ECC_CODEWORDS_PER_BLOCK = [
    [-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
    [-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28],
    [-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
    [-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
  ];
  
  QrCode.NUM_ERROR_CORRECTION_BLOCKS = [
    [-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25],
    [-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49],
    [-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68],
    [-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81],
  ];
  
  function reedSolomonComputeDivisor(degree) {
    if (degree < 1 || degree > 255) throw "Degree out of range";
    var result = [];
    for (var i = 0; i < degree - 1; i++) result.push(0);
    result.push(1);
    var root = 1;
    for (var i = 0; i < degree; i++) {
      for (var j = 0; j < result.length; j++) {
        result[j] = reedSolomonMultiply(result[j], root);
        if (j + 1 < result.length) result[j] ^= result[j + 1];
      }
      root = reedSolomonMultiply(root, 0x02);
    }
    return result;
  }
  
  function reedSolomonComputeRemainder(data, divisor) {
    var result = divisor.map(function() { return 0; });
    data.forEach(function(b) {
      var factor = b ^ result.shift();
      result.push(0);
      divisor.forEach(function(coef, i) { result[i] ^= reedSolomonMultiply(coef, factor); });
    });
    return result;
  }
  
  function reedSolomonMultiply(x, y) {
    if (x >>> 8 !== 0 || y >>> 8 !== 0) throw "Byte out of range";
    var z = 0;
    for (var i = 7; i >= 0; i--) {
      z = (z << 1) ^ ((z >>> 7) * 0x11D);
      z ^= ((y >>> i) & 1) * x;
    }
    return z;
  }
  
  function QrSegment(mode, numChars, bitData) {
    if (numChars < 0) throw "Invalid argument";
    this.mode = mode;
    this.numChars = numChars;
    this.bitData = bitData;
  }
  
  QrSegment.prototype.getData = function() { return this.bitData.slice(); };
  
  QrSegment.makeBytes = function(data) {
    var bb = [];
    data.forEach(function(b) { appendBits(b, 8, bb); });
    return new QrSegment(QrSegment.Mode.BYTE, data.length, bb);
  };
  
  QrSegment.makeNumeric = function(digits) {
    if (!/^[0-9]*$/.test(digits)) throw "String contains non-numeric characters";
    var bb = [];
    for (var i = 0; i < digits.length; ) {
      var n = Math.min(digits.length - i, 3);
      appendBits(parseInt(digits.substr(i, n), 10), n * 3 + 1, bb);
      i += n;
    }
    return new QrSegment(QrSegment.Mode.NUMERIC, digits.length, bb);
  };
  
  QrSegment.makeAlphanumeric = function(text) {
    if (!/^[A-Z0-9 $%*+.\/:-]*$/.test(text)) throw "String contains unencodable characters";
    var bb = [];
    var i;
    for (i = 0; i + 2 <= text.length; i += 2) {
      var temp = QrSegment.ALPHANUMERIC_CHARSET.indexOf(text.charAt(i)) * 45;
      temp += QrSegment.ALPHANUMERIC_CHARSET.indexOf(text.charAt(i + 1));
      appendBits(temp, 11, bb);
    }
    if (i < text.length)
      appendBits(QrSegment.ALPHANUMERIC_CHARSET.indexOf(text.charAt(i)), 6, bb);
    return new QrSegment(QrSegment.Mode.ALPHANUMERIC, text.length, bb);
  };
  
  QrSegment.makeSegments = function(text) {
    if (text === "") return [];
    else if (/^[0-9]*$/.test(text)) return [QrSegment.makeNumeric(text)];
    else if (/^[A-Z0-9 $%*+.\/:-]*$/.test(text)) return [QrSegment.makeAlphanumeric(text)];
    else {
      // Encode as UTF-8 bytes
      var bytes = [];
      for (var i = 0; i < text.length; i++) {
        var c = text.charCodeAt(i);
        if (c < 0x80) bytes.push(c);
        else if (c < 0x800) { bytes.push(0xC0 | c >> 6); bytes.push(0x80 | c & 0x3F); }
        else if (c < 0xD800 || c >= 0xE000) { bytes.push(0xE0 | c >> 12); bytes.push(0x80 | c >> 6 & 0x3F); bytes.push(0x80 | c & 0x3F); }
        else {
          i++;
          c = 0x10000 + ((c & 0x3FF) << 10) + (text.charCodeAt(i) & 0x3FF);
          bytes.push(0xF0 | c >> 18);
          bytes.push(0x80 | c >> 12 & 0x3F);
          bytes.push(0x80 | c >> 6 & 0x3F);
          bytes.push(0x80 | c & 0x3F);
        }
      }
      return [QrSegment.makeBytes(bytes)];
    }
  };
  
  QrSegment.getTotalBits = function(segs, version) {
    var result = 0;
    for (var i = 0; i < segs.length; i++) {
      var seg = segs[i];
      var ccbits = seg.mode.numCharCountBits(version);
      if (seg.numChars >= (1 << ccbits)) return Infinity;
      result += 4 + ccbits + seg.bitData.length;
    }
    return result;
  };
  
  QrSegment.Mode = {
    NUMERIC: { modeBits: 0x1, numCharCountBits: function(ver) { return ver < 10 ? 10 : ver < 27 ? 12 : 14; } },
    ALPHANUMERIC: { modeBits: 0x2, numCharCountBits: function(ver) { return ver < 10 ? 9 : ver < 27 ? 11 : 13; } },
    BYTE: { modeBits: 0x4, numCharCountBits: function(ver) { return ver < 10 ? 8 : 16; } },
  };
  
  QrSegment.ALPHANUMERIC_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:";
  
  return { QrCode: QrCode, QrSegment: QrSegment };
})();

/* === 05-utils.js === */
// ============================================================
// 05-utils.js — Utilidades (formato, escape, etc.)
// ============================================================

function fmt(n) {
  if (n === undefined || n === null || isNaN(n)) return 'S/ 0';
  return 'S/ ' + (n % 1 === 0 ? n.toFixed(0) : n.toFixed(2));
}

function fmt2(n) {
  if (n === undefined || n === null || isNaN(n)) return '0.00';
  return n.toFixed(2);
}

function escapeHtml(str) {
  if (str === undefined || str === null) return '';
  const div = document.createElement('div');
  div.textContent = String(str);
  return div.innerHTML;
}

function iniciales(nombre) {
  if (!nombre) return '***';
  const partes = nombre.trim().split(/\s+/);
  return partes.map(p => p.charAt(0).toUpperCase() + '***').join(' ');
}

// Generar SVG de QR usando qrcodegen
function generarQRSvg(text) {
  try {
    const qr = qrcodegen.QrCode.encodeText(text, qrcodegen.QrCode.Ecc.MEDIUM);
    const size = qr.size;
    const cell = 3;
    const pad = 12;
    const total = size * cell + pad * 2;
    let rects = '';
    for (let r = 0; r < size; r++) {
      for (let c = 0; c < size; c++) {
        if (qr.getModule(c, r)) {
          rects += `<rect x="${pad + c * cell}" y="${pad + r * cell}" width="${cell}" height="${cell}" fill="#000"/>`;
        }
      }
    }
    return `
      <svg class="b-qr-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${total} ${total}" width="140" height="140">
        <rect width="${total}" height="${total}" fill="#fff"/>
        ${rects}
      </svg>
    `;
  } catch (e) {
    console.warn('[utils] Error generando QR:', e);
    return `<div class="b-qr-placeholder">QR no disponible</div>`;
  }
}

/* === 06-navigation.js === */
// ============================================================
// 06-navigation.js — Transiciones de pantalla + flujo contextual
// ============================================================

// Tabla de "volver" desde cada pantalla
// dado el estado actual, ¿a dónde lleva el botón ←?
const VOLVER_DESDE = {
  login: null,           // No hay vuelta atrás desde login
  dictar: 'login',       // Vuelve a login (cambiar vendedor)
  items: 'dictar',
  metodo: 'items',
  verificar: 'metodo',
  boleta: 'verificar',   // En PREVIEW vuelve a verificar; en EMITIDA no se puede volver
};

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

  // Actualizar visibilidad del botón ← según pantalla
  actualizarBotonVolver(nombre);

  setTimeout(() => {
    document.querySelectorAll('.pantalla:not(.activa)').forEach(p => {
      p.classList.remove('saliente-izquierda', 'entrante-izquierda');
    });
  }, 500);
}

// Decide si mostrar/ocultar botón ← según contexto
function actualizarBotonVolver(pantalla) {
  // Si estamos en boleta EMITIDA, ocultar el ← (no se puede volver)
  if (pantalla === 'boleta') {
    const emitida = estado.boletaPreview && estado.boletaPreview.emitida;
    const btnVolver = document.querySelector('#p-boleta .topbar-btn[data-volver]');
    if (btnVolver) {
      btnVolver.style.visibility = emitida ? 'hidden' : 'visible';
    }
  }
}

// Manejador unificado del botón volver
function manejarVolver() {
  const desde = estado.pantalla;
  const destino = VOLVER_DESDE[desde];

  if (!destino) {
    // No hay vuelta atrás definida (login o boleta emitida)
    return;
  }

  // Lógica especial según contexto
  if (desde === 'metodo' && estado.metodosPago.length > 0) {
    // Hay pagos parciales acumulados, confirmar
    confirmarModal({
      titulo: '¿Volver y descartar pagos?',
      texto: 'Se perderán los pagos parciales ya registrados en esta venta.',
      textoBotonSi: 'Sí, volver',
      icono: '↩',
      onConfirmar: () => {
        estado.metodosPago = [];
        irA(destino, { sentido: 'izquierda' });
      },
    });
    return;
  }

  if (desde === 'verificar' && estado.metodosPago.length > 0) {
    confirmarModal({
      titulo: '¿Volver y descartar pagos?',
      texto: 'Se perderán los pagos ya registrados en esta venta.',
      textoBotonSi: 'Sí, volver',
      icono: '↩',
      onConfirmar: () => {
        estado.metodosPago = [];
        irA(destino, { sentido: 'izquierda' });
      },
    });
    return;
  }

  if (desde === 'dictar') {
    // Volver a login = cambiar de vendedor
    confirmarModal({
      titulo: '¿Cambiar de vendedor?',
      texto: 'Tu sesión actual se cerrará.',
      textoBotonSi: 'Sí, cambiar',
      icono: '🚪',
      onConfirmar: () => {
        estado.vendedor = null;
        estado.pin = '';
        actualizarPinDisplay();
        irA('login', { sentido: 'izquierda' });
      },
    });
    return;
  }

  // Navegación normal
  irA(destino, { sentido: 'izquierda' });
}

/* === 07-toast.js === */
// ============================================================
// 07-toast.js — Notificaciones + ConfirmarModal reutilizable
// ============================================================

let toastTimer = null;
function toast(mensaje, tipo = '') {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = mensaje;
  el.className = 'toast visible ' + tipo;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('visible'), 2800);
}

// ============================================================
// MODAL CONFIRMAR (reemplaza confirm() nativo)
// ============================================================
let confirmarCallback = null;

function confirmarModal({ titulo, texto, textoBotonSi, textoBotonNo, icono, onConfirmar }) {
  document.getElementById('modal-confirmar-titulo').textContent = titulo || '¿Confirmar?';
  document.getElementById('modal-confirmar-texto').textContent = texto || '';
  document.getElementById('modal-confirmar-icono').textContent = icono || '⚠';
  document.getElementById('btn-confirmar-si').textContent = textoBotonSi || 'Sí';
  document.getElementById('btn-confirmar-no').textContent = textoBotonNo || 'Cancelar';
  confirmarCallback = onConfirmar;
  document.getElementById('modal-confirmar').classList.remove('oculto');
}

function cerrarModalConfirmar() {
  document.getElementById('modal-confirmar').classList.add('oculto');
  confirmarCallback = null;
}

function setupModalConfirmar() {
  document.getElementById('btn-confirmar-si').addEventListener('click', () => {
    sonidoTap();
    const cb = confirmarCallback;
    cerrarModalConfirmar();
    if (cb) cb();
  });
  document.getElementById('btn-confirmar-no').addEventListener('click', () => {
    sonidoTap();
    cerrarModalConfirmar();
  });
  document.getElementById('modal-confirmar').addEventListener('click', (e) => {
    // Cerrar al tocar fuera de la card
    if (e.target.id === 'modal-confirmar') {
      cerrarModalConfirmar();
    }
  });
}

/* === 08-connection.js === */
// ============================================================
// 08-connection.js — Online/offline + Push silencioso de pagos
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
    if (texto) texto.textContent = 'En línea';
  } else {
    bar.classList.add('offline');
    if (topbar) topbar.classList.add('offline');
    if (texto) texto.textContent = 'Sin conexión';
  }
}

function setupConexion() {
  window.addEventListener('online', actualizarConexion);
  window.addEventListener('offline', actualizarConexion);
}

// ============================================================
// PUSH SILENCIOSO DE PAGOS
// ============================================================
function limpiarPagosExpirados() {
  const ahora = Date.now();
  const limite = ahora - (PAGOS_VENTANA_MIN * 60 * 1000);
  const antes = estado.pagosEnMemoria.length;
  estado.pagosEnMemoria = estado.pagosEnMemoria.filter(p => p.timestamp > limite);
  if (estado.pagosEnMemoria.length > PAGOS_MAX) {
    estado.pagosEnMemoria = estado.pagosEnMemoria
      .sort((a, b) => b.timestamp - a.timestamp)
      .slice(0, PAGOS_MAX);
  }
  return antes !== estado.pagosEnMemoria.length;
}

function agregarPagoEnMemoria(pago) {
  limpiarPagosExpirados();
  if (estado.pagosEnMemoria.some(p => p.nOp === pago.nOp)) return;
  estado.pagosEnMemoria.push({
    ...pago,
    timestamp: Date.now(),
  });
  actualizarContadorPagos();
}

function actualizarContadorPagos() {
  limpiarPagosExpirados();
  const count = estado.pagosEnMemoria.length;
  const elCount = document.getElementById('pagos-disponibles-count');
  if (elCount) elCount.textContent = count;
}

function buscarEnMemoria(nOp) {
  limpiarPagosExpirados();
  return estado.pagosEnMemoria.find(p => p.nOp === nOp);
}

function simularPagoEntrante() {
  const nOp = String(Math.floor(Math.random() * 90000000) + 10000000);
  const monto = +(Math.floor(Math.random() * 20000) / 100 + 5).toFixed(2);
  const operador = OPERADORES[Math.floor(Math.random() * OPERADORES.length)];
  const nombre = NOMBRES_DEMO[Math.floor(Math.random() * NOMBRES_DEMO.length)];
  agregarPagoEnMemoria({ nOp, monto, operador, nombre });
  return { nOp, monto, operador, nombre };
}

/* === 09-auth.js === */
// ============================================================
// 09-auth.js — Login: RUC, PIN, vendedores
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
  document.getElementById('empresa-nombre-txt').textContent = empresa.nombreComercial;
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
  guardarUltimoRuc(ruc);

  document.getElementById('vendedor-nombre').textContent = vendedor.nombre;
  document.getElementById('local-display').textContent = estado.empresa.nombreComercial + ' · ' + vendedor.serieB;
  estado.pin = '';
  actualizarPinDisplay();
  actualizarConexion();
  irA('dictar');
  actualizarSugerencias();

  // Si había venta en curso pendiente, ofrecer recuperarla
  const data = cargarEstadoPersistido();
  if (data && data.ventaEnCurso) {
    setTimeout(() => {
      confirmarModal({
        titulo: '¿Continuar venta pendiente?',
        texto: `Hay una venta sin grabar de ${fmt(data.ventaEnCurso.total)} del ${new Date(data.ventaEnCurso.timestamp).toLocaleString('es-PE')}.`,
        textoBotonSi: 'Sí, continuar',
        textoBotonNo: 'No, descartar',
        icono: '🛒',
        onConfirmar: () => {
          estado.items = data.ventaEnCurso.items;
          estado.total = data.ventaEnCurso.total;
          estado.metodosPago = data.ventaEnCurso.metodosPago || [];
          estado.cliente = data.ventaEnCurso.cliente || { tipoDoc: 'ninguno', numero: '', nombre: '' };
          renderItems();
          irA('items');
          toast('Venta recuperada', 'exito');
        },
      });
    }, 600);
  } else {
    setTimeout(() => toast('Bienvenido ' + vendedor.nombre, 'exito'), 200);
  }
}

function setupLogin() {
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

  // Pre-llenar RUC con el último usado o el demo
  const ultimoRuc = leerUltimoRuc();
  if (ultimoRuc) {
    document.getElementById('input-ruc').value = ultimoRuc;
    validarRucEnVivo(ultimoRuc);
  } else {
    document.getElementById('input-ruc').value = '20615446565';
    validarRucEnVivo('20615446565');
  }
}

/* === 10-dictar.js === */
// ============================================================
// 10-dictar.js — Web Speech API + parser de venta
// ============================================================

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
    const enCat = estado.catalogo.find(c => c.nombre.toLowerCase() === item.nombre.toLowerCase());
    if (enCat && enCat.precioUnit) {
      item.precioUnit = enCat.precioUnit;
    }
  });
  return { items, total };
}

// Sugerencias del catálogo
function actualizarSugerencias() {
  const cont = document.getElementById('sugerencias');
  const chips = document.getElementById('sugerencias-chips');
  if (!cont || !chips) return;
  if (estado.catalogo.length === 0) {
    cont.classList.add('oculto');
    return;
  }
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
    renderItems();
    irA('items');
  } else {
    toast(`+ ${prod.nombre}`, 'exito');
  }
}

// Speech recognition
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;
let escuchando = false;

function setupSpeech() {
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
}

function setupDictar() {
  setupSpeech();

  document.getElementById('btn-dictar').addEventListener('click', () => {
    sonidoTap();
    if (!recognition) { toast('Tu navegador no soporta dictado', 'error'); return; }
    if (escuchando) recognition.stop();
    else { try { recognition.start(); } catch (e) {} }
  });

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
}

/* === 11-items.js === */
// ============================================================
// 11-items.js — Carrito, edición inline, cálculo inteligente
// ============================================================

function recalcularTotal() {
  const sinPrecio = estado.items.filter(i => !i.precioUnit);

  if (sinPrecio.length === 0) {
    let suma = 0;
    estado.items.forEach(i => {
      i.subtotal = i.cantidad * i.precioUnit;
      i.calculado = false;
      suma += i.subtotal;
    });
    estado.total = Math.round(suma * 100) / 100;
  } else if (sinPrecio.length === 1 && estado.total > 0) {
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
}

function renderItems() {
  const lista = document.getElementById('items-lista');
  if (!lista) return;
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
  const hint = document.getElementById('total-hint');
  if (hint) hint.classList.toggle('oculto', estado.total > 0);
}

function actualizarTotalDisplay() {
  const el = document.getElementById('items-total-btn');
  if (el) el.textContent = fmt(estado.total);
}

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
  if (!display) return;
  if (!isNaN(cant) && !isNaN(precio) && precio > 0) {
    display.textContent = `Subtotal: S/ ${fmt2(cant * precio)}`;
  } else {
    display.textContent = 'Subtotal: sin precio unitario';
  }
}

function setupItems() {
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

  // Botón Cobrar
  document.getElementById('btn-cobrar').addEventListener('click', () => {
    if (estado.total < 0.5) {
      sonidoError();
      toast('Toca el total para ingresarlo', 'error');
      return;
    }
    iniciarCobro();
  });

  document.getElementById('btn-rehacer').addEventListener('click', () => {
    estado.items = [];
    estado.total = 0;
    document.getElementById('texto-venta').value = '';
    irA('dictar', { sentido: 'izquierda' });
  });
}

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
  if (!cont || !lista) return;
  if (estado.metodosPago.length === 0) {
    cont.classList.add('oculto');
    document.getElementById('metodos-titulo-txt').textContent = '¿Cómo te paga?';
    return;
  }
  lista.innerHTML = '';
  estado.metodosPago.forEach(p => {
    const li = document.createElement('li');
    li.className = 'pago-item';
    const nombre = { yape: 'Yape', plin: 'Plin', efectivo: 'Efectivo', transferencia: 'Transferencia', tarjeta: 'Tarjeta' }[p.metodo];
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

/* === 12-payment.js === */
// ============================================================
// 12-payment.js — Modal de pago: Yape/Plin/Transfer/Tarjeta/Efectivo
// ============================================================

function abrirModalPago(metodo) {
  const empresa = estado.empresa;
  const cobrado = estado.metodosPago.reduce((s, p) => s + p.monto, 0);
  const falta = +(estado.total - cobrado).toFixed(2);

  if (falta <= 0.001) {
    sonidoError();
    toast('Ya cobraste el total. Continúa al cliente.', 'error');
    return;
  }
  if (estado.total < 0.5) {
    sonidoError();
    toast('Primero ingresa el total a cobrar', 'error');
    return;
  }

  estado.montoPagoActual = falta;
  estado.modalPagoMetodo = metodo;

  const cont = document.getElementById('modal-pago-contenido');
  const acc = document.getElementById('modal-pago-acciones');

  if (metodo === 'yape' || metodo === 'plin') {
    const datos = empresa[metodo];
    const nombreMet = metodo === 'yape' ? 'YAPE' : 'PLIN';
    const colorClass = metodo === 'yape' ? 'pago-yape' : 'pago-plin';
    const qrHtml = datos.qrUrl
      ? `<img src="${datos.qrUrl}" class="pago-qr-img" alt="QR ${nombreMet}">`
      : `<div class="pago-qr-placeholder">
           <div class="pago-qr-icono">📱</div>
           <div class="pago-qr-texto">QR ${nombreMet}</div>
           <div class="pago-qr-sub">Sube tu QR en<br>pagook.pro/admin</div>
         </div>`;
    cont.className = 'modal-pago-contenido ' + colorClass;
    cont.innerHTML = `
      <div class="pago-titulo">Cliente paga por ${nombreMet}</div>
      <div class="pago-qr-wrap">${qrHtml}</div>
      <div class="pago-celular-label">N° celular ${nombreMet}</div>
      <div class="pago-celular">${datos.celular}</div>
      <div class="pago-titular">${escapeHtml(datos.titular)}</div>
      <div class="pago-monto-grande">
        <small>Falta cobrar</small>
        <strong>S/ ${fmt2(falta)}</strong>
      </div>
    `;
    acc.innerHTML = `
      <button class="btn-principal btn-principal-grande" id="btn-pago-continuar">
        <span>Ya ${metodo === 'yape' ? 'yapearon' : 'plinearon'}, verificar</span>
        <span class="btn-flecha">→</span>
      </button>
    `;
  }
  else if (metodo === 'efectivo') {
    cont.className = 'modal-pago-contenido pago-efectivo';
    cont.innerHTML = `
      <div class="pago-titulo">Cobro en EFECTIVO</div>
      <div class="pago-monto-grande">
        <small>Falta cobrar</small>
        <strong>S/ ${fmt2(falta)}</strong>
      </div>
      <label class="campo-label" style="margin-top: 16px;">Monto recibido</label>
      <input
        type="text"
        id="modal-efectivo-monto"
        class="campo-input campo-grande"
        value="${falta.toFixed(2)}"
        inputmode="decimal"
        autocomplete="off">
      <div id="modal-efectivo-vuelto" class="pago-vuelto oculto"></div>
    `;
    acc.innerHTML = `
      <button class="btn-principal btn-principal-grande" id="btn-pago-continuar">
        <span>Confirmar efectivo</span>
        <span class="btn-flecha">✓</span>
      </button>
    `;
  }
  else if (metodo === 'transferencia') {
    const cuentas = empresa.cuentasBancarias || [];
    const tabsHtml = cuentas.map((c, i) => `
      <button class="pago-tab ${i === 0 ? 'activa' : ''}" data-cuenta-idx="${i}">
        ${escapeHtml(c.banco)}
      </button>
    `).join('');
    const cuentasHtml = cuentas.map((c, i) => `
      <div class="pago-cuenta-panel ${i === 0 ? 'activa' : 'oculto'}" data-cuenta-idx="${i}">
        <div class="pago-cuenta-tipo">${escapeHtml(c.tipo)}</div>
        <div class="pago-cuenta-num" data-copy="${c.numero}">${escapeHtml(c.numero)}</div>
        <div class="pago-cuenta-label">CCI</div>
        <div class="pago-cuenta-cci" data-copy="${c.cci}">${escapeHtml(c.cci)}</div>
        <div class="pago-cuenta-label">Titular</div>
        <div class="pago-cuenta-titular">${escapeHtml(c.titular)}</div>
      </div>
    `).join('');
    cont.className = 'modal-pago-contenido pago-transferencia';
    cont.innerHTML = `
      <div class="pago-titulo">TRANSFERENCIA BANCARIA</div>
      <div class="pago-tabs">${tabsHtml}</div>
      <div class="pago-cuentas">${cuentasHtml}</div>
      <div class="pago-monto-grande">
        <small>Falta cobrar</small>
        <strong>S/ ${fmt2(falta)}</strong>
      </div>
    `;
    acc.innerHTML = `
      <button class="btn-principal btn-principal-grande" id="btn-pago-continuar">
        <span>Ya transfirieron, verificar</span>
        <span class="btn-flecha">→</span>
      </button>
    `;
  }
  else if (metodo === 'tarjeta') {
    const obligatorio = empresa.tarjetaConfig && empresa.tarjetaConfig.voucherObligatorio;
    cont.className = 'modal-pago-contenido pago-tarjeta';
    cont.innerHTML = `
      <div class="pago-titulo">Cobro con TARJETA</div>
      <div class="pago-tarjeta-icono">💳</div>
      <div class="pago-tarjeta-instr">Pase la tarjeta del cliente<br>por su POS físico</div>
      <div class="pago-monto-grande">
        <small>Falta cobrar</small>
        <strong>S/ ${fmt2(falta)}</strong>
      </div>
      <label class="campo-label" style="margin-top: 14px;">
        N° de voucher POS
        ${obligatorio ? '<span class="label-obligatorio">(obligatorio)</span>' : '<span class="label-opcional">(opcional)</span>'}
      </label>
      <input
        type="text"
        id="modal-tarjeta-voucher"
        class="campo-input"
        placeholder="Ej: 458921"
        inputmode="numeric"
        autocomplete="off">
    `;
    acc.innerHTML = `
      <button class="btn-secundario" id="btn-pago-tarjeta-rechazada">Rechazada, volver</button>
      <button class="btn-principal btn-principal-grande" id="btn-pago-continuar">
        <span>Aprobada, registrar</span>
        <span class="btn-flecha">✓</span>
      </button>
    `;
  }

  document.getElementById('modal-pago').classList.remove('oculto');
}

function cerrarModalPago() {
  document.getElementById('modal-pago').classList.add('oculto');
  estado.modalPagoMetodo = null;
}

function actualizarVueltoEfectivo() {
  const input = document.getElementById('modal-efectivo-monto');
  const display = document.getElementById('modal-efectivo-vuelto');
  if (!input || !display) return;
  const monto = parseFloat(input.value.replace(',', '.'));
  const falta = estado.montoPagoActual;
  if (isNaN(monto)) { display.classList.add('oculto'); return; }
  if (monto > falta) {
    display.classList.remove('oculto');
    display.innerHTML = `Vuelto: <strong>S/ ${fmt2(monto - falta)}</strong>`;
  } else {
    display.classList.add('oculto');
  }
}

function agregarPago(metodo, monto, nOperacion) {
  estado.metodosPago.push({
    metodo,
    monto,
    nOperacion: nOperacion || null,
    foto: estado.foto,
  });
}

function procesarContinuarPago(metodo) {
  if (metodo === 'efectivo') {
    const input = document.getElementById('modal-efectivo-monto');
    const monto = parseFloat(input.value.replace(',', '.'));
    if (isNaN(monto) || monto < 0.5) { sonidoError(); toast('Monto inválido', 'error'); return; }
    cerrarModalPago();
    const falta = estado.montoPagoActual;
    const aRegistrar = Math.min(monto, falta);
    agregarPago('efectivo', aRegistrar, null);
    setTimeout(() => mostrarResultadoEfectivo(monto, falta), 50);
  }
  else if (metodo === 'tarjeta') {
    const empresa = estado.empresa;
    const obligatorio = empresa.tarjetaConfig && empresa.tarjetaConfig.voucherObligatorio;
    const voucher = document.getElementById('modal-tarjeta-voucher').value.trim();
    if (obligatorio && voucher.length < 3) {
      sonidoError();
      toast('El n° de voucher es obligatorio (mín. 3 dígitos)', 'error');
      return;
    }
    cerrarModalPago();
    const falta = estado.montoPagoActual;
    agregarPago('tarjeta', falta, voucher || null);
    setTimeout(() => mostrarResultadoTarjeta(falta, voucher), 50);
  }
  else {
    // Yape, Plin, Transferencia → ir a pantalla Verificar
    cerrarModalPago();
    setTimeout(() => irAPantallaVerificar(metodo), 50);
  }
}

function setupModalPago() {
  // Event delegation completa para el modal
  document.getElementById('modal-pago').addEventListener('click', (e) => {
    // Botón cerrar
    if (e.target.closest('#btn-modal-pago-cerrar')) {
      sonidoTap();
      cerrarModalPago();
      return;
    }
    // Botón continuar
    if (e.target.closest('#btn-pago-continuar')) {
      e.preventDefault();
      e.stopPropagation();
      sonidoTap();
      const metodo = estado.modalPagoMetodo;
      if (metodo) procesarContinuarPago(metodo);
      else console.error('[modal-pago] estado.modalPagoMetodo está vacío');
      return;
    }
    // Botón tarjeta rechazada
    if (e.target.closest('#btn-pago-tarjeta-rechazada')) {
      sonidoTap();
      cerrarModalPago();
      return;
    }
    // Pestañas bancos
    const tab = e.target.closest('.pago-tab');
    if (tab) {
      const idx = tab.dataset.cuentaIdx;
      document.querySelectorAll('.pago-tab').forEach(t => t.classList.remove('activa'));
      tab.classList.add('activa');
      document.querySelectorAll('.pago-cuenta-panel').forEach(p => {
        if (p.dataset.cuentaIdx === idx) {
          p.classList.remove('oculto');
          p.classList.add('activa');
        } else {
          p.classList.remove('activa');
          p.classList.add('oculto');
        }
      });
      sonidoTap();
      return;
    }
    // Copiar al portapapeles
    const copyEl = e.target.closest('[data-copy]');
    if (copyEl) {
      const txt = copyEl.dataset.copy;
      sonidoTap();
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(txt)
          .then(() => toast('Copiado: ' + txt, 'exito'))
          .catch(() => toast('No se pudo copiar', 'error'));
      } else {
        toast('No se pudo copiar', 'error');
      }
      return;
    }
  });

  // Input efectivo (delegation)
  document.getElementById('modal-pago').addEventListener('input', (e) => {
    if (e.target.id === 'modal-efectivo-monto') {
      actualizarVueltoEfectivo();
    }
  });

  // Botones de método de pago
  document.querySelectorAll('.metodo').forEach(btn => {
    btn.addEventListener('click', () => {
      sonidoTap();
      const metodo = btn.dataset.metodo;
      abrirModalPago(metodo);
    });
  });
}

/* === 13-verify.js === */
// ============================================================
// 13-verify.js — Verificación: memoria + BD, resultados
// ============================================================

function irAPantallaVerificar(metodo) {
  estado.nOperacion = '';
  estado.foto = null;
  document.getElementById('input-operacion').value = '';
  document.getElementById('foto-preview').classList.add('oculto');
  document.getElementById('foto-fuente').classList.add('oculto');
  document.getElementById('input-foto-camara').value = '';
  document.getElementById('input-foto-galeria').value = '';
  document.getElementById('btn-verificar').disabled = true;
  document.getElementById('resultado-card').classList.add('oculto');
  document.getElementById('verificacion-form').classList.remove('oculto');

  const falta = estado.montoPagoActual;
  const titulos = {
    yape: 'Verificar Yape',
    plin: 'Verificar Plin',
    transferencia: 'Verificar Transferencia',
  };
  document.getElementById('verificar-titulo').textContent = titulos[metodo];
  document.getElementById('verificar-monto').textContent = 'falta S/ ' + fmt2(falta);
  document.getElementById('display-monto-cobrar').textContent = 'S/ ' + fmt2(falta);
  document.getElementById('display-monto-pagado').textContent = '--';
  document.getElementById('display-monto-pagado').classList.remove('verde', 'rojo');

  irA('verificar');
  setTimeout(() => document.getElementById('input-operacion').focus(), 500);
}

function validarVerificacion() {
  const operacion = document.getElementById('input-operacion').value.trim();
  const ok = operacion.length >= 4;
  document.getElementById('btn-verificar').disabled = !ok;
}

function procesarFoto(file, fuente) {
  if (!file) return;
  sonidoTap();
  const reader = new FileReader();
  reader.onload = (ev) => {
    estado.foto = ev.target.result;
    const preview = document.getElementById('foto-preview');
    preview.src = ev.target.result;
    preview.classList.remove('oculto');
    const fuenteEl = document.getElementById('foto-fuente');
    fuenteEl.textContent = fuente === 'camara' ? '✓ Foto tomada' : '✓ Foto subida desde galería';
    fuenteEl.classList.remove('oculto');
    validarVerificacion();
  };
  reader.readAsDataURL(file);
}

// Mock: simula búsqueda en backend con timeout
function mockBuscarPago(nOp, montoEsperado) {
  return new Promise((resolve) => {
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

async function validarConTimeout(nOp, monto) {
  if (!estado.online) {
    return { offline: true };
  }
  try {
    const timeoutPromise = new Promise((_, reject) => {
      setTimeout(() => reject(new Error('timeout')), TIMEOUT_VALIDACION_MS);
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

// ============================================================
// Resultado: actualizar display "Monto pagado" + navegación
// ============================================================
function actualizarMontoPagadoDisplay(monto, color) {
  const el = document.getElementById('display-monto-pagado');
  if (!el) return;
  el.textContent = 'S/ ' + fmt2(monto);
  el.classList.remove('verde', 'rojo');
  if (color) el.classList.add(color);
}

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
  document.getElementById('btn-pendiente').classList.toggle('oculto', !botones.pendiente);

  document.getElementById('verificacion-form').classList.add('oculto');
  card.classList.remove('oculto');

  if (tipo === 'exito') sonidoExito();
  else if (tipo === 'advertencia') sonidoAdvertencia();
  else sonidoError();
}

function mostrarResultadoOK(r, monto) {
  actualizarMontoPagadoDisplay(monto, 'verde');
  const cobrado = estado.metodosPago.reduce((s, p) => s + p.monto, 0);
  const falta = estado.total - cobrado;
  if (falta > 0.01) {
    mostrarResultado('advertencia', '¡Pago parcial recibido!',
      `Recibimos ${fmt(monto)}. Faltan ${fmt(falta)} para completar.`,
      { completar: true });
  } else {
    sonidoExito();
    irAClienteBoleta();
  }
}

function mostrarResultadoParcial(r) {
  actualizarMontoPagadoDisplay(r.monto, 'rojo');
  const cobrado = estado.metodosPago.reduce((s, p) => s + p.monto, 0);
  const falta = estado.total - cobrado;
  if (falta > 0.01) {
    mostrarResultado('advertencia', 'Llegó menos de lo esperado',
      `Recibimos ${fmt(r.monto)}. Faltan ${fmt(falta)}.`,
      { completar: true });
  } else {
    sonidoExito();
    irAClienteBoleta();
  }
}

function mostrarResultadoOffline(monto) {
  actualizarMontoPagadoDisplay(monto, 'verde');
  const cobrado = estado.metodosPago.reduce((s, p) => s + p.monto, 0);
  const falta = estado.total - cobrado;
  if (falta > 0.01) {
    mostrarResultado('advertencia', 'Pago registrado (sin verificar)',
      `Sin conexión. Registramos ${fmt(monto)}. Se verificará cuando vuelva internet.`,
      { completar: true });
  } else {
    sonidoExito();
    toast('Sin conexión — venta guardada, se verificará después', '');
    setTimeout(() => irAClienteBoleta(), 800);
  }
}

function mostrarResultadoTimeout(monto) {
  actualizarMontoPagadoDisplay(monto, 'verde');
  const cobrado = estado.metodosPago.reduce((s, p) => s + p.monto, 0);
  const falta = estado.total - cobrado;
  const msg = `${fmt(monto)} registrado. La verificación se completará en segundo plano.`;
  if (falta > 0.01) {
    mostrarResultado('advertencia', 'Pago registrado', msg, { completar: true });
  } else {
    sonidoExito();
    toast('Verificación demorada — venta registrada', '');
    setTimeout(() => irAClienteBoleta(), 800);
  }
}

function mostrarResultadoNoEncontrado(monto) {
  actualizarMontoPagadoDisplay(0, 'rojo');
  mostrarResultado('error', 'No encontrado',
    `No hay registro de operación ${estado.nOperacion} en los últimos ${PAGOS_VENTANA_MIN} minutos.`,
    { pendiente: true });
}

function mostrarResultadoEfectivo(montoRecibido, falta) {
  const vuelto = montoRecibido - falta;
  const cobrado = estado.metodosPago.reduce((s, p) => s + p.monto, 0);
  const faltaAhora = estado.total - cobrado;

  if (faltaAhora > 0.01) {
    irA('verificar');
    document.getElementById('verificacion-form').classList.add('oculto');
    mostrarResultado('advertencia', 'Efectivo registrado',
      `S/ ${fmt2(montoRecibido)} en efectivo. Faltan S/ ${fmt2(faltaAhora)}.`, { completar: true });
  } else {
    sonidoExito();
    if (vuelto > 0.001) {
      toast(`Vuelto: S/ ${fmt2(vuelto)}`, 'exito');
    }
    setTimeout(() => irAClienteBoleta(), vuelto > 0.001 ? 1500 : 400);
  }
}

function mostrarResultadoTarjeta(monto, voucher) {
  const cobrado = estado.metodosPago.reduce((s, p) => s + p.monto, 0);
  const faltaAhora = estado.total - cobrado;
  const ref = voucher ? ` (voucher ${voucher})` : '';
  if (faltaAhora > 0.01) {
    irA('verificar');
    document.getElementById('verificacion-form').classList.add('oculto');
    mostrarResultado('advertencia', 'Tarjeta registrada',
      `S/ ${fmt2(monto)}${ref}. Faltan S/ ${fmt2(faltaAhora)}.`, { completar: true });
  } else {
    sonidoExito();
    toast(`Tarjeta aprobada${ref}`, 'exito');
    setTimeout(() => irAClienteBoleta(), 600);
  }
}

function setupVerify() {
  document.getElementById('input-operacion').addEventListener('input', (e) => {
    estado.nOperacion = e.target.value.trim();
    validarVerificacion();
  });

  document.getElementById('input-foto-camara').addEventListener('change', (e) => {
    procesarFoto(e.target.files[0], 'camara');
  });

  document.getElementById('input-foto-galeria').addEventListener('change', (e) => {
    procesarFoto(e.target.files[0], 'galeria');
  });

  document.getElementById('btn-verificar').addEventListener('click', async () => {
    const monto = estado.montoPagoActual;
    const nOp = estado.nOperacion;
    document.getElementById('btn-verificar').disabled = true;
    document.getElementById('btn-verificar').querySelector('span').textContent = 'Verificando...';

    // PRIMERO: buscar en memoria
    const enMemoria = buscarEnMemoria(nOp);
    if (enMemoria) {
      document.getElementById('btn-verificar').disabled = false;
      document.getElementById('btn-verificar').querySelector('span').textContent = 'Verificar pago';
      const operadorOk = (estado.modalPagoMetodo === enMemoria.operador) ||
                          (estado.metodosPago.length > 0 && estado.metodosPago[estado.metodosPago.length-1] &&
                           estado.metodosPago[estado.metodosPago.length-1].metodo === enMemoria.operador);
      // Para v8: el método actual está en estado.modalPagoMetodo o se preserva
      // Simplifico: si encuentra y monto coincide, OK; si no, ver lógica
      if (enMemoria.monto < monto) {
        agregarPago(enMemoria.operador, enMemoria.monto, nOp);
        mostrarResultadoParcial({ monto: enMemoria.monto, remitente: iniciales(enMemoria.nombre) });
      } else {
        agregarPago(enMemoria.operador, monto, nOp);
        mostrarResultadoOK({ monto: enMemoria.monto, remitente: iniciales(enMemoria.nombre), hora: 'recién' }, monto);
      }
      return;
    }

    // SEGUNDO: fallback a BD
    const r = await validarConTimeout(nOp, monto);

    document.getElementById('btn-verificar').disabled = false;
    document.getElementById('btn-verificar').querySelector('span').textContent = 'Verificar pago';

    // Determinar el método actual de la venta (último elegido)
    const metodoActual = estado.modalPagoMetodo || 'yape';

    if (r.offline) {
      agregarPago(metodoActual, monto, nOp);
      mostrarResultadoOffline(monto);
    } else if (r.timeout) {
      agregarPago(metodoActual, monto, nOp);
      mostrarResultadoTimeout(monto);
    } else if (!r.encontrado) {
      mostrarResultadoNoEncontrado(monto);
    } else if (r.monto < monto) {
      agregarPago(metodoActual, r.monto, nOp);
      mostrarResultadoParcial(r);
    } else {
      agregarPago(metodoActual, monto, nOp);
      mostrarResultadoOK(r, monto);
    }
  });

  document.getElementById('btn-completar-pago').addEventListener('click', () => {
    sonidoTap();
    irA('metodo', { sentido: 'izquierda' });
  });
}

/* === 14-client.js === */
// ============================================================
// 14-client.js — Datos del cliente, consulta DNI/RUC
// ============================================================

function irAClienteBoleta() {
  estado.cliente = { tipoDoc: 'ninguno', numero: '', nombre: '' };
  document.querySelectorAll('.tipo-doc-btn').forEach(b => b.classList.remove('activo'));
  document.querySelector('.tipo-doc-btn[data-tipo="ninguno"]').classList.add('activo');
  document.getElementById('doc-input-grupo').classList.add('oculto');
  document.getElementById('input-doc').value = '';
  document.getElementById('input-cliente-nombre').value = '';
  document.getElementById('cliente-form').classList.remove('oculto');
  document.getElementById('boleta-vista').classList.add('oculto');
  document.getElementById('boleta-estado-preview').classList.add('oculto');
  document.getElementById('boleta-estado-emitida').classList.add('oculto');
  document.getElementById('cliente-consulta-estado').classList.add('oculto');
  irA('boleta');
}

// Mock consulta SUNAT/RENIEC
function consultarPersona(tipo, numero) {
  return new Promise((resolve) => {
    const delay = 400 + Math.random() * 400;
    setTimeout(() => {
      const nombre = PERSONAS_DEMO[numero];
      if (nombre) resolve({ encontrado: true, nombre, numero });
      else resolve({ encontrado: false, numero });
    }, delay);
  });
}

function setupClient() {
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
        document.getElementById('cliente-consulta-estado').classList.add('oculto');
        setTimeout(() => input.focus(), 100);
      }
    });
  });

  document.getElementById('input-doc').addEventListener('input', async (e) => {
    e.target.value = e.target.value.replace(/\D/g, '');
    estado.cliente.numero = e.target.value;
    const num = e.target.value;
    const tipo = estado.cliente.tipoDoc;
    const longitudOK = (tipo === 'dni' && num.length === 8) || (tipo === 'ruc' && num.length === 11);
    const estadoEl = document.getElementById('cliente-consulta-estado');
    if (!longitudOK) {
      estadoEl.classList.add('oculto');
      return;
    }
    estadoEl.classList.remove('oculto');
    estadoEl.className = 'cliente-consulta consultando';
    estadoEl.innerHTML = '<span class="cliente-spinner"></span> Consultando ' + tipo.toUpperCase() + '...';
    const r = await consultarPersona(tipo, num);
    if (document.getElementById('input-doc').value !== num) return;
    if (r.encontrado) {
      document.getElementById('input-cliente-nombre').value = r.nombre;
      estado.cliente.nombre = r.nombre;
      estadoEl.className = 'cliente-consulta exito';
      estadoEl.innerHTML = '✓ Encontrado en ' + (tipo === 'dni' ? 'RENIEC' : 'SUNAT');
      tono(880, 0.1, 'sine', 0.12);
    } else {
      estadoEl.className = 'cliente-consulta error';
      estadoEl.innerHTML = `No encontrado en ${tipo === 'dni' ? 'RENIEC' : 'SUNAT'}. Ingresa el nombre manualmente.`;
    }
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
}

/* === 15-receipt.js === */
// ============================================================
// 15-receipt.js — Generación boleta + Grabar y emitir + Descartar
// ============================================================

function generarBoleta() {
  const v = estado.vendedor;
  const empresa = estado.empresa;
  const local = estado.localActual;
  const tipoComprobante = estado.cliente.tipoDoc === 'ruc' ? 'F' : 'B';
  const tipoSunatCod = tipoComprobante === 'F' ? '01' : '03';
  const serie = tipoComprobante === 'F' ? v.serieF : v.serieB;
  if (!estado.correlativos[serie]) estado.correlativos[serie] = 0;

  // PREVIEW: correlativo es el siguiente, pero AÚN NO se incrementa
  const correlativo = estado.correlativos[serie] + 1;
  estado.boletaPreview = {
    serie,
    correlativo,
    tipoComprobante,
    tipoSunatCod,
    emitida: false,
  };

  const ahora = new Date();
  const dd = String(ahora.getDate()).padStart(2, '0');
  const mm = String(ahora.getMonth() + 1).padStart(2, '0');
  const yyyy = ahora.getFullYear();
  const hh = String(ahora.getHours()).padStart(2, '0');
  const mi = String(ahora.getMinutes()).padStart(2, '0');
  const fechaCorta = `${dd}/${mm}/${yyyy}`;
  const fechaSunat = `${dd}/${mm}/${String(yyyy).slice(2)}`;

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

  const tipoDocLabel = { dni: 'DNI', ruc: 'RUC' }[estado.cliente.tipoDoc];
  const tipoDocSunat = { dni: '1', ruc: '6', ninguno: '0' }[estado.cliente.tipoDoc];
  const clienteHtml = estado.cliente.tipoDoc !== 'ninguno' ? `
    <div class="b-meta"><strong>${tipoDocLabel}:</strong> ${estado.cliente.numero}</div>
    ${estado.cliente.nombre ? `<div class="b-meta"><strong>Cliente:</strong> ${escapeHtml(estado.cliente.nombre).toUpperCase()}</div>` : ''}
  ` : '';

  const pendienteHtml = !empresa.tieneCDT
    ? `<div class="b-pendiente">PENDIENTE DE EMISIÓN</div>` : '';

  const lineasPagos = estado.metodosPago.map(p => {
    const nombre = { yape: 'YAPE', plin: 'PLIN', efectivo: 'EFECTIVO', transferencia: 'TRANSF', tarjeta: 'TARJETA' }[p.metodo];
    const op = p.nOperacion ? ` op ${p.nOperacion}` : '';
    return `<div class="b-pago-linea"><span>${nombre}${op}</span><span>S/ ${fmt2(p.monto)}</span></div>`;
  }).join('');
  const pagoHtml = `
    <div class="b-pagos-box">
      <div class="b-pagos-titulo">PAGOS</div>
      ${lineasPagos}
    </div>
  `;

  const totalesHtml = aplicaIGV
    ? `<div class="b-tot-linea"><span>Op. gravada:</span><span>S/ ${fmt2(subtotal)}</span></div>
       <div class="b-tot-linea"><span>IGV 18%:</span><span>S/ ${fmt2(igv)}</span></div>
       <div class="b-tot-linea gran-total"><span>TOTAL:</span><span>S/ ${fmt2(totalNum)}</span></div>`
    : `<div class="b-tot-linea"><span>Op. exonerada:</span><span>S/ ${fmt2(subtotal)}</span></div>
       <div class="b-tot-linea gran-total"><span>TOTAL:</span><span>S/ ${fmt2(totalNum)}</span></div>`;

  const direccionHtml = local.esAnexo
    ? `<div class="b-empresa-info">Domicilio fiscal: ${escapeHtml(empresa.domicilioFiscal)}</div>
       <div class="b-empresa-info">Anexo: ${escapeHtml(local.direccion)}</div>`
    : `<div class="b-empresa-info">${escapeHtml(local.direccion)}</div>`;

  const logoHtml = empresa.logoUrl
    ? `<div class="b-logo-box"><img src="${empresa.logoUrl}" class="b-logo-img" alt="Logo"></div>`
    : `<div class="b-logo-box b-logo-placeholder">LOGO</div>`;

  const leyendaHtml = empresa.esAmazonia
    ? `<div class="b-leyenda">"Bienes transferidos y servicios prestados en la Amazonía para ser consumidos en la misma."</div>`
    : '';

  // QR híbrido SUNAT + URL
  const docCliente = estado.cliente.tipoDoc !== 'ninguno' ? estado.cliente.numero : '';
  const urlVerif = `https://facturalo.pro/v/${serie}-${correlativoStr}`;
  const qrData = [
    estado.ruc,
    tipoSunatCod,
    serie,
    correlativoStr,
    fmt2(igv),
    fmt2(totalNum),
    fechaSunat,
    tipoDocSunat,
    docCliente,
    '', // hash vacío offline
  ].join('|') + '|\n' + urlVerif;

  const qrSvg = generarQRSvg(qrData);

  const html = `
    <div class="b-header">
      ${logoHtml}
      <div class="b-header-info">
        <div class="b-empresa">${escapeHtml(empresa.nombreComercial).toUpperCase()}</div>
        <div class="b-empresa-razon">${escapeHtml(empresa.razonSocial)}</div>
        <div class="b-empresa-info">RUC ${estado.ruc}</div>
        ${direccionHtml}
        <div class="b-empresa-info">${escapeHtml(empresa.ciudad)}</div>
      </div>
    </div>
    <div class="b-divider"></div>
    <div class="b-tipo">${tipoLabel}</div>
    <div class="b-numero">${serie} - ${correlativoStr}</div>
    ${pendienteHtml}
    <div class="b-divider"></div>
    <div class="b-meta-row">
      <div class="b-meta-col">
        <div class="b-meta"><strong>Fecha:</strong> ${fechaCorta} ${hh}:${mi}</div>
        <div class="b-meta"><strong>Vendedor:</strong> ${escapeHtml(v.nombre)}</div>
        ${clienteHtml}
      </div>
      <div class="b-meta-col b-meta-col-derecha">
        <div class="b-meta"><strong>Forma de pago:</strong></div>
        <div class="b-meta b-forma-pago">${empresa.formaPago}</div>
      </div>
    </div>
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
    ${leyendaHtml}
    <div class="b-qr-wrap">${qrSvg}</div>
    <div class="b-footer">
      Representación impresa<br>
      Consulta este comprobante en<br>
      <strong>facturalo.pro/v/${serie}-${correlativoStr}</strong>
    </div>
    <div class="b-divider"></div>
    <div class="b-publicidad">
      Emitido con <strong>pagoOK</strong><br>
      <em>La WebApp de los emprendedores en LATAM</em>
    </div>
  `;
  document.getElementById('boleta-papel').innerHTML = html;
  document.getElementById('cliente-form').classList.add('oculto');
  document.getElementById('boleta-vista').classList.remove('oculto');
  document.getElementById('boleta-estado-preview').classList.remove('oculto');
  document.getElementById('boleta-estado-emitida').classList.add('oculto');
  sonidoTap();
  // Actualizar visibilidad del botón ← (en preview sí se puede volver)
  actualizarBotonVolver('boleta');
}

function grabarYEmitir() {
  if (!estado.boletaPreview) return;
  const { serie, correlativo } = estado.boletaPreview;
  estado.correlativos[serie] = correlativo;
  estado.boletaPreview.emitida = true;
  estado.boletaPreview.emitidaEn = new Date().toISOString();
  guardarVenta();
  persistirEstado();
  document.getElementById('boleta-estado-preview').classList.add('oculto');
  document.getElementById('boleta-estado-emitida').classList.remove('oculto');
  document.getElementById('boleta-emitida-msg').textContent =
    `Comprobante ${serie}-${String(correlativo).padStart(8, '0')} emitido`;
  sonidoExito();
  toast('Comprobante grabado y emitido', 'exito');
  // Ocultar botón ← en estado emitida
  actualizarBotonVolver('boleta');
}

function descartarVentaPreview() {
  confirmarModal({
    titulo: '¿Descartar venta?',
    texto: 'Se perderán los items, pagos y datos del cliente. El correlativo NO se usará.',
    textoBotonSi: 'Sí, descartar',
    icono: '🗑',
    onConfirmar: () => {
      toast('Venta descartada', '');
      reiniciarFlujo();
      irA('dictar', { sentido: 'izquierda' });
    },
  });
}

function guardarVenta() {
  const venta = {
    id: 'v_' + Date.now(),
    timestamp: new Date().toISOString(),
    vendedor: estado.vendedor.nombre,
    negocio: estado.empresa.nombreComercial,
    serie: estado.boletaPreview ? estado.boletaPreview.serie : null,
    correlativo: estado.boletaPreview ? estado.boletaPreview.correlativo : null,
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
        existe.precioUnit = item.precioUnit;
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
  persistirEstado();
}

function reiniciarFlujo() {
  limpiarVentaEnCurso();
  document.getElementById('texto-venta').value = '';
  document.getElementById('resultado-card').classList.add('oculto');
  document.getElementById('cliente-form').classList.remove('oculto');
  document.getElementById('boleta-vista').classList.add('oculto');
  document.getElementById('boleta-estado-preview').classList.add('oculto');
  document.getElementById('boleta-estado-emitida').classList.add('oculto');
  actualizarSugerencias();
  persistirEstado();
}

function setupReceipt() {
  // Grabar y emitir
  document.getElementById('btn-grabar-emitir').addEventListener('click', () => {
    sonidoTap();
    grabarYEmitir();
  });

  // Descartar
  document.getElementById('btn-descartar').addEventListener('click', () => {
    sonidoTap();
    descartarVentaPreview();
  });

  // Nueva venta (después de emitir)
  document.getElementById('btn-boleta-finalizar').addEventListener('click', () => {
    sonidoTap();
    reiniciarFlujo();
    irA('dictar', { sentido: 'izquierda' });
  });

  // Resultado: pendiente (cuando no se encontró el pago)
  document.getElementById('btn-pendiente').addEventListener('click', () => {
    guardarVenta();
    reiniciarFlujo();
    irA('dictar', { sentido: 'izquierda' });
    setTimeout(() => toast('Venta pendiente', ''), 200);
  });
}

/* === 16-share.js === */
// ============================================================
// 16-share.js — Compartir e imprimir comprobante
// ============================================================

function generarTextoBoleta() {
  const empresa = estado.empresa;
  const local = estado.localActual;
  const v = estado.vendedor;
  const ahora = new Date();
  const fechaStr = ahora.toLocaleDateString('es-PE') + ' ' + ahora.toLocaleTimeString('es-PE', { hour: '2-digit', minute: '2-digit' });
  const tipo = estado.cliente.tipoDoc === 'ruc' ? 'F' : 'B';
  const serie = estado.boletaPreview ? estado.boletaPreview.serie : (tipo === 'F' ? v.serieF : v.serieB);
  const correlativo = estado.boletaPreview
    ? String(estado.boletaPreview.correlativo).padStart(8, '0')
    : '00000000';

  let txt = `*${empresa.nombreComercial}*\n${empresa.razonSocial}\nRUC ${estado.ruc}\n${local.direccion}\n\n`;
  txt += `*${estado.cliente.tipoDoc === 'ruc' ? 'FACTURA' : 'BOLETA'} DE VENTA*\n${serie} - ${correlativo}\n`;
  if (!empresa.tieneCDT) txt += `_PENDIENTE DE EMISIÓN_\n`;
  txt += `Fecha: ${fechaStr}\nVendedor: ${v.nombre}\nForma de pago: ${empresa.formaPago}\n\n`;
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
  txt += `\n*TOTAL: ${fmt(estado.total)}*\n\nPagos:\n`;
  estado.metodosPago.forEach(p => {
    const nombre = { yape: 'Yape', plin: 'Plin', efectivo: 'Efectivo', transferencia: 'Transferencia', tarjeta: 'Tarjeta' }[p.metodo];
    txt += `  ${nombre}${p.nOperacion ? ' op ' + p.nOperacion : ''}: ${fmt(p.monto)}\n`;
  });
  txt += `\nVerifica en: facturalo.pro/v/${serie}-${correlativo}\n\n_Emitido con pagoOK_`;
  return txt;
}

function setupShare() {
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

  document.getElementById('btn-imprimir').addEventListener('click', () => {
    sonidoTap();
    window.print();
  });
}

/* === 17-menu.js === */
// ============================================================
// 17-menu.js — Menú lateral, simular pago, opciones
// ============================================================

function actualizarMenu() {
  document.getElementById('catalogo-count').textContent = estado.catalogo.length;
  document.getElementById('historial-count').textContent = estado.historial.length;
  actualizarContadorPagos();
  if (estado.vendedor) {
    document.getElementById('menu-info-vendedor').textContent = estado.vendedor.nombre + ' · ' + estado.empresa.nombreComercial;
    document.getElementById('menu-info-serie').textContent = 'SERIE ' + estado.vendedor.serieB + ' / ' + estado.vendedor.serieF;
    document.getElementById('menu-info').classList.remove('oculto');
  } else {
    document.getElementById('menu-info').classList.add('oculto');
  }
  document.getElementById('sonido-icono').textContent = estado.sonidoActivo ? '🔊' : '🔇';
  document.getElementById('sonido-estado').textContent = estado.sonidoActivo ? 'ON' : 'OFF';
}

function setupMenu() {
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

  document.getElementById('menu-pagos-disponibles').addEventListener('click', () => {
    document.getElementById('menu-overlay').classList.add('oculto');
    limpiarPagosExpirados();
    const count = estado.pagosEnMemoria.length;
    if (count === 0) {
      toast('No hay pagos en memoria');
    } else {
      toast(`${count} pago${count !== 1 ? 's' : ''} disponible${count !== 1 ? 's' : ''} en memoria`);
    }
  });

  document.getElementById('menu-simular-pago').addEventListener('click', () => {
    document.getElementById('menu-overlay').classList.add('oculto');
    const p = simularPagoEntrante();
    sonidoTap();
    toast(`🎲 Demo: pago N° ${p.nOp} · S/ ${fmt2(p.monto)} · ${p.operador.toUpperCase()}`);
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
    persistirEstado();
    if (estado.sonidoActivo) sonidoExito();
  });

  document.getElementById('menu-cambiar').addEventListener('click', () => {
    document.getElementById('menu-overlay').classList.add('oculto');
    confirmarModal({
      titulo: '¿Cambiar de vendedor?',
      texto: 'Tu sesión actual se cerrará.',
      textoBotonSi: 'Sí, cambiar',
      icono: '🚪',
      onConfirmar: () => {
        estado.vendedor = null;
        estado.pin = '';
        actualizarPinDisplay();
        irA('login', { sentido: 'izquierda' });
      },
    });
  });
}

/* === 18-main.js === */
// ============================================================
// 18-main.js — Init + wiring de todos los listeners
// ============================================================

(function() {
  'use strict';

  console.log(`[pagoOK Caja] ${CAJA_VERSION} cargado`);

  // ============================================================
  // FIX TECLADO: auto-scroll al input enfocado
  // ============================================================
  function asegurarVisible(el) {
    if (!el) return;
    setTimeout(() => {
      try {
        el.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
      } catch (e) {
        el.scrollIntoView();
      }
    }, 350);
  }

  function setupTeclado() {
    document.querySelectorAll('input[type="text"], input[type="tel"], textarea').forEach(el => {
      el.addEventListener('focus', () => asegurarVisible(el));
    });
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', () => {
        const focused = document.activeElement;
        if (focused && (focused.tagName === 'INPUT' || focused.tagName === 'TEXTAREA')) {
          const rect = focused.getBoundingClientRect();
          if (rect.bottom > window.visualViewport.height - 20) {
            asegurarVisible(focused);
          }
        }
      });
    }
  }

  // ============================================================
  // BOTÓN VOLVER UNIFICADO
  // ============================================================
  function setupBotonesVolver() {
    document.querySelectorAll('[data-volver]').forEach(btn => {
      btn.addEventListener('click', () => {
        sonidoTap();
        manejarVolver();
      });
    });
  }

  // ============================================================
  // PWA INSTALL BANNER
  // ============================================================
  let deferredPrompt = null;
  function setupPWA() {
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
      // Marcar que el usuario rechazó, no mostrar de nuevo en esta sesión
      sessionStorage.setItem('banner_descartado', '1');
    });

    // Si ya fue descartado en esta sesión, no mostrar
    if (sessionStorage.getItem('banner_descartado')) {
      document.getElementById('banner-instalar').classList.add('oculto');
    }

    if ('serviceWorker' in navigator) {
      window.addEventListener('load', () => {
        navigator.serviceWorker.register('sw.js').catch(() => {});
      });
    }
  }

  // ============================================================
  // INIT
  // ============================================================
  cargarEstadoPersistido();
  actualizarConexion();
  setupConexion();
  setupModalConfirmar();
  setupLogin();
  setupDictar();
  setupItems();
  setupModalPago();
  setupVerify();
  setupClient();
  setupReceipt();
  setupShare();
  setupMenu();
  setupBotonesVolver();
  setupTeclado();
  setupPWA();

  // Auto-persistir cada vez que cambia el estado importante
  // (versión simple: cada 30s si hay venta en curso)
  setInterval(() => {
    if (estado.items.length > 0) {
      persistirEstado();
    }
  }, 30000);

  // Activar audio en primera interacción
  document.body.addEventListener('click', function activarAudio() {
    getAudioCtx();
    document.body.removeEventListener('click', activarAudio);
  }, { once: true });

})();