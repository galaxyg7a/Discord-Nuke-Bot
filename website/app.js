// ── GALAXY CANVAS ────────────────────────────────────────────────────────────
(function () {
  const canvas = document.getElementById('star-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  let W, H, stars = [], dustParticles = [], shootingStars = [], nebulaClouds = [];
  let frame = 0;

  const STAR_COUNT   = 700;
  const DUST_COUNT   = 250;
  const NEBULA_CLOUD = 6;

  const STAR_COLORS = [
    [255, 255, 255],
    [200, 180, 255],
    [167, 139, 250],
    [103, 232, 249],
    [236, 72, 153],
    [251, 191, 36],
    [134, 239, 172],
  ];

  function rand(a, b) { return a + Math.random() * (b - a); }
  function randInt(a, b) { return Math.floor(rand(a, b)); }
  function pick(arr) { return arr[randInt(0, arr.length)]; }

  function resize() {
    W = canvas.width  = window.innerWidth;
    H = canvas.height = window.innerHeight;
  }

  // ── MILKY WAY BAND: cluster stars in a diagonal strip ──
  function galaxyX(y) {
    const t = y / H;
    return W * 0.15 + W * 0.7 * t + Math.sin(t * Math.PI * 1.5) * W * 0.12;
  }

  function initStars() {
    stars = [];
    for (let i = 0; i < STAR_COUNT; i++) {
      const inBand = Math.random() < 0.45;
      let x, y;
      if (inBand) {
        y = Math.random() * H;
        x = galaxyX(y) + rand(-W * 0.18, W * 0.18);
      } else {
        x = Math.random() * W;
        y = Math.random() * H;
      }
      const col = pick(STAR_COLORS);
      stars.push({
        x, y,
        origX: x,
        r:     inBand ? rand(0.2, 1.4) : rand(0.3, 2.2),
        alpha: inBand ? rand(0.2, 0.85) : rand(0.25, 1),
        speed: rand(0.03, 0.18),
        twinkle: Math.random() * Math.PI * 2,
        twinkleSpeed: rand(0.006, 0.028),
        col,
        glow: Math.random() < 0.12,
      });
    }

    dustParticles = [];
    for (let i = 0; i < DUST_COUNT; i++) {
      const y = Math.random() * H;
      dustParticles.push({
        x: galaxyX(y) + rand(-W * 0.22, W * 0.22),
        y,
        r: rand(0.1, 0.5),
        alpha: rand(0.04, 0.18),
        twinkle: Math.random() * Math.PI * 2,
        twinkleSpeed: rand(0.003, 0.012),
      });
    }

    nebulaClouds = [];
    for (let i = 0; i < NEBULA_CLOUD; i++) {
      nebulaClouds.push({
        x: Math.random() * W,
        y: Math.random() * H,
        r: rand(60, 200),
        col: pick([[120,40,255],[40,80,255],[200,40,120],[20,160,200],[80,200,100]]),
        alpha: rand(0.015, 0.045),
        speed: rand(0.0003, 0.0008),
        angle: Math.random() * Math.PI * 2,
      });
    }
  }

  function drawNebulaClouds() {
    for (const n of nebulaClouds) {
      n.angle += n.speed;
      n.x += Math.cos(n.angle) * 0.15;
      n.y += Math.sin(n.angle) * 0.1;
      if (n.x < -n.r) n.x = W + n.r;
      if (n.x > W + n.r) n.x = -n.r;
      if (n.y < -n.r) n.y = H + n.r;
      if (n.y > H + n.r) n.y = -n.r;

      const g = ctx.createRadialGradient(n.x, n.y, 0, n.x, n.y, n.r);
      g.addColorStop(0, `rgba(${n.col[0]},${n.col[1]},${n.col[2]},${n.alpha})`);
      g.addColorStop(1, `rgba(${n.col[0]},${n.col[1]},${n.col[2]},0)`);
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
      ctx.fillStyle = g;
      ctx.fill();
    }
  }

  function drawDust() {
    for (const d of dustParticles) {
      d.twinkle += d.twinkleSpeed;
      const a = d.alpha * (0.5 + 0.5 * Math.sin(d.twinkle));
      ctx.beginPath();
      ctx.arc(d.x, d.y, d.r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(180,160,255,${a})`;
      ctx.fill();
    }
  }

  function drawStars() {
    for (const s of stars) {
      s.twinkle += s.twinkleSpeed;
      const a = s.alpha * (0.55 + 0.45 * Math.sin(s.twinkle));

      if (s.glow) {
        const g = ctx.createRadialGradient(s.x, s.y, 0, s.x, s.y, s.r * 5);
        g.addColorStop(0, `rgba(${s.col[0]},${s.col[1]},${s.col[2]},${a * 0.5})`);
        g.addColorStop(1, `rgba(${s.col[0]},${s.col[1]},${s.col[2]},0)`);
        ctx.beginPath();
        ctx.arc(s.x, s.y, s.r * 5, 0, Math.PI * 2);
        ctx.fillStyle = g;
        ctx.fill();
      }

      ctx.beginPath();
      ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${s.col[0]},${s.col[1]},${s.col[2]},${a})`;
      ctx.fill();

      s.y += s.speed * 0.06;
      if (s.y > H) { s.y = 0; s.x = Math.random() * W; }
    }
  }

  function spawnShootingStar() {
    if (shootingStars.length >= 4) return;
    const col = pick(STAR_COLORS);
    shootingStars.push({
      x: rand(0, W * 0.75),
      y: rand(0, H * 0.45),
      vx: rand(5, 13),
      vy: rand(2, 7),
      alpha: 1,
      trail: [],
      col,
      tailLen: randInt(22, 40),
    });
  }

  function drawShootingStars() {
    for (let i = shootingStars.length - 1; i >= 0; i--) {
      const ss = shootingStars[i];
      ss.trail.push({ x: ss.x, y: ss.y });
      if (ss.trail.length > ss.tailLen) ss.trail.shift();
      ss.x += ss.vx;
      ss.y += ss.vy;
      ss.alpha -= 0.016;

      if (ss.alpha <= 0 || ss.x > W + 50 || ss.y > H + 50) {
        shootingStars.splice(i, 1);
        continue;
      }

      for (let j = 0; j < ss.trail.length; j++) {
        const t = ss.trail[j];
        const tf = j / ss.trail.length;
        const size = 2.2 * tf;
        ctx.beginPath();
        ctx.arc(t.x, t.y, size, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${ss.col[0]},${ss.col[1]},${ss.col[2]},${ss.alpha * tf * 0.7})`;
        ctx.fill();
      }

      const g = ctx.createRadialGradient(ss.x, ss.y, 0, ss.x, ss.y, 4);
      g.addColorStop(0, `rgba(255,255,255,${ss.alpha})`);
      g.addColorStop(0.4, `rgba(${ss.col[0]},${ss.col[1]},${ss.col[2]},${ss.alpha * 0.8})`);
      g.addColorStop(1, `rgba(${ss.col[0]},${ss.col[1]},${ss.col[2]},0)`);
      ctx.beginPath();
      ctx.arc(ss.x, ss.y, 4, 0, Math.PI * 2);
      ctx.fillStyle = g;
      ctx.fill();
    }
  }

  function loop() {
    ctx.clearRect(0, 0, W, H);
    drawNebulaClouds();
    drawDust();
    drawStars();
    drawShootingStars();
    frame++;
    if (frame % 120 === 0) spawnShootingStar();
    requestAnimationFrame(loop);
  }

  window.addEventListener('resize', () => { resize(); initStars(); });
  resize();
  initStars();
  loop();

  setTimeout(spawnShootingStar, 1200);
  setTimeout(spawnShootingStar, 3500);
})();

// ── SCROLL FADE-IN ───────────────────────────────────────────────────────────
(function () {
  const els = document.querySelectorAll('.fade-up');
  if (!els.length) return;
  const io = new IntersectionObserver((entries) => {
    entries.forEach(e => { if (e.isIntersecting) { e.target.classList.add('visible'); io.unobserve(e.target); } });
  }, { threshold: 0.12 });
  els.forEach(el => io.observe(el));
})();

// ── ACTIVE NAV LINK ──────────────────────────────────────────────────────────
(function () {
  const path = window.location.pathname.split('/').pop() || 'index.html';
  document.querySelectorAll('.nav-links a').forEach(a => {
    const href = a.getAttribute('href');
    if (href === path || (path === '' && href === 'index.html')) {
      a.classList.add('active');
    }
  });
})();

// ── HAMBURGER MENU ────────────────────────────────────────────────────────────
(function () {
  const btn = document.querySelector('.hamburger');
  const links = document.querySelector('.nav-links');
  if (!btn || !links) return;
  btn.addEventListener('click', () => {
    btn.classList.toggle('open');
    links.classList.toggle('open');
  });
  links.querySelectorAll('a').forEach(a => {
    a.addEventListener('click', () => {
      btn.classList.remove('open');
      links.classList.remove('open');
    });
  });
})();
