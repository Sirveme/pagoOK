
// ============ PRODUCTOS ROTANDO ============
const productos = [
  { emoji: '👖', nombre: 'Jean talla 32', precio: 'S/ 45.00', comprador: 'Carlos M.', banco: 'Yape', bancoLetra: 'Y' },
  { emoji: '🐟', nombre: 'Ceviche para 2', precio: 'S/ 52.00', comprador: 'María R.', banco: 'Yape', bancoLetra: 'Y' },
  { emoji: '🍛', nombre: 'Menú del día × 2', precio: 'S/ 28.00', comprador: 'Luis P.', banco: 'Plin', bancoLetra: 'P' },
  { emoji: '💊', nombre: 'Paracetamol + vitamina C', precio: 'S/ 18.50', comprador: 'Ana G.', banco: 'Yape', bancoLetra: 'Y' },
  { emoji: '🔩', nombre: '2 kg de tornillos', precio: 'S/ 22.00', comprador: 'José H.', banco: 'Plin', bancoLetra: 'P' },
  { emoji: '🛢️', nombre: 'Cambio de aceite', precio: 'S/ 80.00', comprador: 'Pedro L.', banco: 'Yape', bancoLetra: 'Y' },
];

let productoIndex = 0;
let sonidoActivo = true;
let sonidoRepeticiones = 0;
const MAX_REPETICIONES = 2;

// "Ding" generado con Web Audio
function ding() {
  if (!sonidoActivo || sonidoRepeticiones >= MAX_REPETICIONES) return;
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(1100, ctx.currentTime + 0.1);
    gain.gain.setValueAtTime(0.2, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.4);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.4);
    sonidoRepeticiones++;
  } catch (e) {}
}

function toggleSonido() {
  sonidoActivo = !sonidoActivo;
  const btn = document.getElementById('sonidoBtn');
  btn.textContent = sonidoActivo ? '🔊 Sonido: ON' : '🔇 Sonido: OFF';
  if (sonidoActivo) sonidoRepeticiones = 0;
}

function actualizarProducto() {
  const p = productos[productoIndex];
  document.getElementById('producto-emoji').textContent = p.emoji;
  document.getElementById('producto-nombre').textContent = p.nombre;
  document.getElementById('producto-precio').textContent = p.precio;
  document.getElementById('texto-notif').textContent = `Te ${p.banco === 'Yape' ? 'yapearon' : 'plinearon'} ${p.precio} de ${p.comprador}`;
  document.getElementById('confirmado-monto').textContent = p.precio;
  document.getElementById('confirmado-de').textContent = `de ${p.comprador}`;
  // Actualizar logo del banco en la notificación
  const logoBanco = document.querySelector('.logo-banco');
  if (logoBanco) logoBanco.textContent = p.bancoLetra;
  const titulo = document.querySelector('.notif-dueño .titulo');
  if (titulo) titulo.textContent = p.banco;
  // Cambiar el total
  const nuevoTotal = 340 + productoIndex * 10;
  document.getElementById('monto-total').textContent = `S/ ${nuevoTotal}`;
}

function ciclarDemo() {
  const notif = document.getElementById('notif-dueño');
  const esperando = document.getElementById('esperando');
  const confirmado = document.getElementById('confirmado');
  
  // Actualizar producto
  actualizarProducto();
  
  // Reset
  notif.style.animation = 'none';
  confirmado.classList.remove('activo');
  esperando.style.display = 'flex';
  void notif.offsetWidth;
  void confirmado.offsetWidth;
  
  // Restart
  notif.style.animation = 'aparecer 0.4s ease-out 1s forwards';
  setTimeout(() => {
    esperando.style.display = 'none';
    confirmado.classList.add('activo');
    ding();
  }, 2600);
  
  productoIndex = (productoIndex + 1) % productos.length;
}

// Arranque
setTimeout(() => {
  ding();
}, 2700);

// Loop solo si sección visible
const demoSection = document.querySelector('.demo-section');
const observer = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    demoSection.dataset.visible = entry.isIntersecting ? 'true' : 'false';
  });
}, { threshold: 0.3 });
observer.observe(demoSection);

setInterval(() => {
  if (demoSection.dataset.visible === 'true') {
    ciclarDemo();
  }
}, 9000);

// ============ FAQ ACORDEÓN ============
document.querySelectorAll('.faq-item').forEach(item => {
  item.querySelector('.faq-pregunta').addEventListener('click', () => {
    item.classList.toggle('abierto');
  });
});

// ============ FORM ENCUESTA ============
document.getElementById('formEncuesta').addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  const mensaje = document.getElementById('formMensaje');
  const submitBtn = form.querySelector('.form-submit');
  
  const datos = {
    ciudad: form.ciudad.value,
    tipo_negocio: form.tipo_negocio.value,
    tiene_vendedores: form.tiene_vendedores?.value || null,
    ha_dudado: form.ha_dudado?.value || null,
    contacto: form.contacto.value,
    comentario: form.comentario.value,
    origen: 'landing_pagook',
  };
  
  submitBtn.disabled = true;
  submitBtn.textContent = 'Enviando...';
  
  try {
    const res = await fetch(`${window.PAGOOK_CONFIG.apiBase}/api/v1/encuestas/inbound`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(datos),
    });
    
    if (res.ok) {
      mensaje.className = 'form-mensaje exito';
      mensaje.textContent = '✓ ¡Gracias! Tu respuesta fue registrada. Te contactaremos pronto.';
      form.reset();
    } else {
      throw new Error('Servidor respondió con error');
    }
  } catch (err) {
    mensaje.className = 'form-mensaje error';
    mensaje.textContent = '✗ Hubo un problema. Por favor intenta de nuevo o escríbenos por WhatsApp.';
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = 'Enviar mis respuestas';
    setTimeout(() => { mensaje.className = 'form-mensaje'; }, 6000);
  }
});

// ============ COMPARTIR ============
async function compartir() {
  const datos = {
    title: 'pagoOK - Valida Yape y Plin al instante',
    text: '¿Tu vendedor te dice "sí me yapearon"? Con pagoOK lo confirmas en 2 segundos. Desde S/5 al mes.',
    url: 'https://pagook.pro',
  };
  if (navigator.share) {
    try { await navigator.share(datos); } catch (e) {}
  } else {
    try {
      await navigator.clipboard.writeText(datos.url);
      alert('Link copiado: ' + datos.url);
    } catch (e) {
      prompt('Copia este link:', datos.url);
    }
  }
}

document.getElementById('btnCompartir')?.addEventListener('click', compartir);
document.getElementById('sonidoBtn')?.addEventListener('click', toggleSonido);