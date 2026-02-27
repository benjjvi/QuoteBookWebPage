(() => {
  // Guard for pages without the background canvas and keep scope local.
  const canvas = document.getElementById("bg-canvas");
  if (!canvas) return;

  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  let width, height;
  let particles = [];
  const MAX_PARTICLE_COUNT = 120;
  const MIN_PARTICLE_COUNT = 36;
  const PARTICLE_AREA = 22000;
  const MAX_DISTANCE = 120;
  const MAX_DISTANCE_SQUARED = MAX_DISTANCE * MAX_DISTANCE;
  let particleFill = "rgba(255, 255, 255, 0.7)";
  let particleLineRgb = "255, 255, 255";
  let animationFrameId = null;
  let isAnimating = false;

  function getDocumentHeight() {
    const body = document.body;
    const html = document.documentElement;
    return Math.max(
      body.scrollHeight,
      body.offsetHeight,
      html.clientHeight,
      html.scrollHeight,
      html.offsetHeight,
    );
  }

  function resize() {
    width = canvas.width = window.innerWidth;
    height = canvas.height = Math.max(window.innerHeight, getDocumentHeight());
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    init();
  }

  window.addEventListener("resize", resize);
  resize();

  const resolveThemeColors = () => {
    const styles = getComputedStyle(document.documentElement);
    const fill = styles.getPropertyValue("--qb-particle-fill").trim();
    const rgb = styles.getPropertyValue("--qb-particle-rgb").trim();
    particleFill = fill || "rgba(255, 255, 255, 0.7)";
    particleLineRgb = rgb || "255, 255, 255";
  };

  resolveThemeColors();
  document.addEventListener("qb-theme-change", resolveThemeColors);

  class Particle {
    constructor() {
      this.x = Math.random() * width;
      this.y = Math.random() * height;
      this.vx = (Math.random() - 0.5) * 0.4;
      this.vy = (Math.random() - 0.5) * 0.4;
      this.radius = 1.5;
    }

    move() {
      this.x += this.vx;
      this.y += this.vy;

      if (this.x < 0 || this.x > width) this.vx *= -1;
      if (this.y < 0 || this.y > height) this.vy *= -1;
    }

    draw() {
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.radius, 0, Math.PI * 2);
      ctx.fillStyle = particleFill;
      ctx.fill();
    }
  }

  function init() {
    const viewportArea = Math.max(1, width * Math.max(window.innerHeight, 1));
    const nextCount = Math.min(
      MAX_PARTICLE_COUNT,
      Math.max(MIN_PARTICLE_COUNT, Math.floor(viewportArea / PARTICLE_AREA)),
    );
    particles = [];
    for (let i = 0; i < nextCount; i++) {
      particles.push(new Particle());
    }
  }

  function connect() {
    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        const dx = particles[i].x - particles[j].x;
        const dy = particles[i].y - particles[j].y;
        const distanceSquared = dx * dx + dy * dy;
        if (distanceSquared < MAX_DISTANCE_SQUARED) {
          const opacity = 1 - Math.sqrt(distanceSquared) / MAX_DISTANCE;
          ctx.strokeStyle = `rgba(${particleLineRgb}, ${
            opacity
          })`;
          ctx.lineWidth = 0.5;
          ctx.beginPath();
          ctx.moveTo(particles[i].x, particles[i].y);
          ctx.lineTo(particles[j].x, particles[j].y);
          ctx.stroke();
        }
      }
    }
  }

  function animate() {
    if (!isAnimating) return;
    ctx.clearRect(0, 0, width, height);

    particles.forEach((particle) => {
      particle.move();
      particle.draw();
    });

    connect();
    if (!isAnimating) return;
    animationFrameId = requestAnimationFrame(animate);
  }

  function startAnimation() {
    if (isAnimating) return;
    isAnimating = true;
    animate();
  }

  function stopAnimation() {
    isAnimating = false;
    if (animationFrameId !== null) {
      cancelAnimationFrame(animationFrameId);
      animationFrameId = null;
    }
  }

  const prefersReducedMotion = window.matchMedia(
    "(prefers-reduced-motion: reduce)",
  ).matches;

  if (!prefersReducedMotion) {
    init();
    startAnimation();
  } else {
    // Respect reduced motion preference: no animation.
  }

  document.addEventListener("visibilitychange", () => {
    if (prefersReducedMotion) return;
    if (document.hidden) {
      stopAnimation();
    } else {
      startAnimation();
    }
  });

  if ("ResizeObserver" in window) {
    const observer = new ResizeObserver(() => resize());
    observer.observe(document.body);
  }

  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => resize());
  }
})();
