// ── STAR FIELD ──────────────────────────────────────────────────────────────
(function () {
  const canvas = document.getElementById('star-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  let W, H, stars = [], shootingStars = [];

  const STAR_COUNT = 320;

  function resize() {
    W = canvas.width  = window.innerWidth;
    H = canvas.height = window.innerHeight;
  }

  function randBetween(a, b) { return a + Math.random() * (b - a); }

  function initStars() {
    stars = [];
    for (let i = 0; i < STAR_COUNT; i++) {
      stars.push({
        x:     Math.random() * W,
        y:     Math.random() * H,
        r:     randBetween(0.3, 1.8),
        alpha: randBetween(0.3, 1),
        speed: randBetween(0.08, 0.25),
        twinkle: Math.random() * Math.PI * 2,
        twinkleSpeed: randBetween(0.008, 0.025),
        hue:   Math.random() > 0.85 ? (Math.random() > 0.5 ? '#a78bfa' : '#67e8f9') : '#ffffff',
      });
    }
  }

  function spawnShootingStar() {
    if (shootingStars.length >= 3) return;
    const startX = randBetween(0, W * 0.8);
    const startY = randBetween(0, H * 0.4);
    shootingStars.push({
      x: startX, y: startY,
      vx: randBetween(4, 9),
      vy: randBetween(2, 5),
      len: randBetween(80, 200),
      alpha: 1,
      trail: [],
    });
  }

  function drawStars() {
    ctx.clearRect(0, 0, W, H);

    // draw static + twinkling stars
    for (const s of stars) {
      s.twinkle += s.twinkleSpeed;
      const a = s.alpha * (0.6 + 0.4 * Math.sin(s.twinkle));
      ctx.beginPath();
      ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
      ctx.fillStyle = s.hue === '#ffffff'
        ? `rgba(255,255,255,${a})`
        : s.hue === '#a78bfa'
          ? `rgba(167,139,250,${a})`
          : `rgba(103,232,249,${a})`;
      ctx.fill();

      // drift downward slowly
      s.y += s.speed * 0.08;
      if (s.y > H) { s.y = 0; s.x = Math.random() * W; }
    }

    // draw shooting stars
    for (let i = shootingStars.length - 1; i >= 0; i--) {
      const ss = shootingStars[i];
      ss.trail.push({ x: ss.x, y: ss.y });
      if (ss.trail.length > 18) ss.trail.shift();
      ss.x += ss.vx;
      ss.y += ss.vy;
      ss.alpha -= 0.022;

      if (ss.alpha <= 0 || ss.x > W || ss.y > H) {
        shootingStars.splice(i, 1);
        continue;
      }

      // draw trail
      for (let j = 0; j < ss.trail.length; j++) {
        const t = ss.trail[j];
        const tf = j / ss.trail.length;
        ctx.beginPath();
        ctx.arc(t.x, t.y, 1.2 * tf, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(200,180,255,${ss.alpha * tf * 0.8})`;
        ctx.fill();
      }

      ctx.beginPath();
      ctx.arc(ss.x, ss.y, 1.8, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(230,220,255,${ss.alpha})`;
      ctx.fill();
    }
  }

  let frame = 0;
  function loop() {
    drawStars();
    frame++;
    if (frame % 180 === 0) spawnShootingStar();
    requestAnimationFrame(loop);
  }

  window.addEventListener('resize', () => { resize(); initStars(); });
  resize();
  initStars();
  loop();

  // first shooting star after 2s
  setTimeout(spawnShootingStar, 2000);
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
