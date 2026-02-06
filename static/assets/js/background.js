(() => {
  // Guard for pages without the background canvas and keep scope local.
  const canvas = document.getElementById("bg-canvas");
  if (!canvas) return;

  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  let width, height;
  let particles = [];
  const PARTICLE_COUNT = 200;
  const MAX_DISTANCE = 120;

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
  }

  window.addEventListener("resize", resize);
  resize();

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
      ctx.fillStyle = "rgba(255, 255, 255, 0.7)";
      ctx.fill();
    }
  }

  function init() {
    particles = [];
    for (let i = 0; i < PARTICLE_COUNT; i++) {
      particles.push(new Particle());
    }
  }

  function connect() {
    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        const dx = particles[i].x - particles[j].x;
        const dy = particles[i].y - particles[j].y;
        const distance = Math.sqrt(dx * dx + dy * dy);

        if (distance < MAX_DISTANCE) {
          ctx.strokeStyle = `rgba(255, 255, 255, ${
            1 - distance / MAX_DISTANCE
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
    ctx.clearRect(0, 0, width, height);

    particles.forEach((particle) => {
      particle.move();
      particle.draw();
    });

    connect();
    requestAnimationFrame(animate);
  }

  const prefersReducedMotion = window.matchMedia(
    "(prefers-reduced-motion: reduce)",
  ).matches;

  if (!prefersReducedMotion) {
    init();
    animate();
  } else {
    // Respect reduced motion preference: no animation.
  }

  if ("ResizeObserver" in window) {
    const observer = new ResizeObserver(() => resize());
    observer.observe(document.body);
  }

  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => resize());
  }
})();
