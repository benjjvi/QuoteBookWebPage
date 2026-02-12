(() => {
  const configEl = document.getElementById("qbIndexConfig");
  let config = {};

  if (configEl?.textContent) {
    try {
      config = JSON.parse(configEl.textContent);
    } catch (_err) {
      config = {};
    }
  }

  const isSecureOrigin = () => {
    const host = window.location.hostname;
    return (
      window.location.protocol === "https:" ||
      host === "localhost" ||
      host === "127.0.0.1"
    );
  };

  const isStandalone = () =>
    (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) ||
    window.navigator.standalone;

  const urlBase64ToUint8Array = (base64String) => {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const rawData = atob(base64);
    const outputArray = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; i += 1) {
      outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
  };

  const setupInstallPrompt = () => {
    const installBtn = document.getElementById("installBtn");
    const installHelp = document.getElementById("installHelp");
    const installStatus = document.getElementById("installStatus");
    if (!installBtn) return;

    let deferredPrompt = null;

    const setStatus = (message) => {
      if (!installStatus) return;
      installStatus.textContent = message || "";
      installStatus.style.display = message ? "" : "none";
    };

    const showHelp = (html) => {
      if (!installHelp) return;
      installHelp.hidden = false;
      installHelp.innerHTML = html;
    };

    const hideHelp = () => {
      if (!installHelp) return;
      installHelp.hidden = true;
      installHelp.innerHTML = "";
    };

    const updateInstalledState = () => {
      const installed = isStandalone();
      installBtn.style.display = installed ? "none" : "";
      if (installed) {
        installBtn.disabled = true;
        installBtn.textContent = "Installed";
        setStatus("");
        hideHelp();
      } else {
        installBtn.disabled = false;
        installBtn.textContent = "Install app";
        setStatus("");
      }
    };

    const buildHelp = () => {
      const ua = navigator.userAgent || "";
      const isIOS = /iPhone|iPad|iPod/i.test(ua);
      const isMac = /Macintosh/i.test(ua);
      const isSafari = /Safari/i.test(ua) && !/Chrome|CriOS|Edg|OPR|OPiOS/i.test(ua);

      if (isIOS && isSafari) {
        return `
          <strong>Install on iPhone/iPad:</strong><br />
          Tap the Share icon, then choose <em>Add to Home Screen</em>.
        `;
      }

      if (isMac && isSafari) {
        return `
          <strong>Install on macOS Safari:</strong><br />
          Use <em>File → Add to Dock</em>.
        `;
      }

      return `
        <strong>Install instructions:</strong><br />
        Open the browser menu and look for <em>Install</em> or <em>Add to Home Screen</em>.
      `;
    };

    updateInstalledState();
    window.addEventListener("visibilitychange", () => {
      if (!document.hidden) updateInstalledState();
    });

    if (!isSecureOrigin()) {
      installBtn.disabled = true;
      setStatus("Install requires HTTPS.");
      showHelp(
        "<strong>Install unavailable:</strong><br />This feature needs HTTPS (or localhost)."
      );
      return;
    }

    window.addEventListener("beforeinstallprompt", (event) => {
      event.preventDefault();
      deferredPrompt = event;
      setStatus("Install available.");
    });

    window.addEventListener("appinstalled", () => {
      deferredPrompt = null;
      updateInstalledState();
    });

    installBtn.addEventListener("click", async () => {
      if (deferredPrompt) {
        deferredPrompt.prompt();
        try {
          const choice = await deferredPrompt.userChoice;
          setStatus(choice?.outcome === "accepted" ? "Install accepted." : "Install dismissed.");
        } catch (_err) {
          setStatus("Install prompt failed.");
        } finally {
          deferredPrompt = null;
        }
        return;
      }

      showHelp(buildHelp());
    });
  };

  const setupPushPrompt = () => {
    const vapidPublicKey = String(config.vapidPublicKey || "");
    let pushSubscribeToken = String(config.pushSubscribeToken || "");
    const modal = document.getElementById("notifyModal");
    const acceptBtn = document.getElementById("notifyAccept");
    const declineBtn = document.getElementById("notifyDecline");
    const statusEl = document.getElementById("notifyStatus");

    if (!modal || !acceptBtn || !declineBtn || !statusEl) return;

    const supportsPush =
      "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

    const setStatus = (message) => {
      statusEl.textContent = message || "";
    };

    const showModal = () => {
      modal.hidden = false;
      modal.classList.add("is-visible");
      modal.setAttribute("aria-hidden", "false");
    };

    const hideModal = () => {
      modal.hidden = true;
      modal.classList.remove("is-visible");
      modal.setAttribute("aria-hidden", "true");
      setStatus("");
    };

    const shouldPrompt = async () => {
      if (!isStandalone()) return false;
      if (!supportsPush) return false;
      if (!vapidPublicKey) return false;
      if (!navigator.onLine) return false;
      if (!isSecureOrigin()) return false;
      if (Notification.permission === "denied") return false;

      const declinedAt = Number(localStorage.getItem("qb_push_declined_at") || "0");
      if (declinedAt && Date.now() - declinedAt < 24 * 60 * 60 * 1000) return false;

      try {
        const reg = await navigator.serviceWorker.ready;
        const existing = await reg.pushManager.getSubscription();
        if (existing) {
          localStorage.setItem("qb_push_prompted", "accepted");
          return false;
        }
      } catch (_err) {
        return false;
      }

      return true;
    };

    const refreshSubscribeToken = async () => {
      const response = await fetch("/api/push/token", {
        method: "GET",
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error("Unable to refresh subscribe token");
      const data = await response.json();
      if (!data.token) throw new Error("Missing subscribe token");
      pushSubscribeToken = data.token;
    };

    const subscribeUser = async () => {
      const reg = await navigator.serviceWorker.ready;
      const subscription = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(vapidPublicKey),
      });

      const sendSubscribe = async () =>
        fetch("/api/push/subscribe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({
            subscription,
            userAgent: navigator.userAgent,
            token: pushSubscribeToken,
          }),
        });

      let response = await sendSubscribe();
      if (response.status === 403) {
        await refreshSubscribeToken();
        response = await sendSubscribe();
      }
      if (!response.ok) throw new Error("Subscription failed");
    };

    acceptBtn.addEventListener("click", async () => {
      setStatus("Requesting permission...");
      try {
        const permission = await Notification.requestPermission();
        if (permission !== "granted") {
          if (permission === "denied") {
            setStatus("Notifications blocked.");
            localStorage.setItem("qb_push_declined_at", String(Date.now()));
          } else {
            setStatus("Notification prompt dismissed.");
          }
          return;
        }

        await subscribeUser();
        localStorage.setItem("qb_push_prompted", "accepted");
        localStorage.removeItem("qb_push_declined_at");
        setStatus("Notifications enabled.");
        setTimeout(() => hideModal(), 900);
      } catch (_err) {
        setStatus("Unable to enable notifications.");
        localStorage.removeItem("qb_push_prompted");
      }
    });

    declineBtn.addEventListener("click", () => {
      localStorage.setItem("qb_push_declined_at", String(Date.now()));
      hideModal();
    });

    window.addEventListener("load", async () => {
      if (await shouldPrompt()) showModal();
    });
  };

  const setupEmailSubscribePrompt = () => {
    let emailSubscribeToken = String(config.emailSubscribeToken || "");
    const modal = document.getElementById("emailModal");
    const form = document.getElementById("emailSubscribeForm");
    const input = document.getElementById("emailSubscribeInput");
    const acceptBtn = document.getElementById("emailSubscribeAccept");
    const declineBtn = document.getElementById("emailSubscribeDecline");
    const statusEl = document.getElementById("emailSubscribeStatus");

    if (!modal || !form || !input || !acceptBtn || !declineBtn || !statusEl) return;

    const DECLINE_COOLDOWN_MS = 7 * 24 * 60 * 60 * 1000;

    const setStatus = (message) => {
      statusEl.textContent = message || "";
    };

    const showModal = () => {
      modal.hidden = false;
      modal.classList.add("is-visible");
      modal.setAttribute("aria-hidden", "false");
      input.focus();
    };

    const hideModal = () => {
      modal.hidden = true;
      modal.classList.remove("is-visible");
      modal.setAttribute("aria-hidden", "true");
      setStatus("");
    };

    const hasVisiblePrompt = () =>
      Boolean(document.querySelector(".notify-modal.is-visible"));

    const shouldPrompt = () => {
      if (!navigator.onLine) return false;
      if (localStorage.getItem("qb_email_subscribed") === "true") return false;
      const declinedAt = Number(localStorage.getItem("qb_email_declined_at") || "0");
      if (declinedAt && Date.now() - declinedAt < DECLINE_COOLDOWN_MS) return false;
      return true;
    };

    const refreshSubscribeToken = async () => {
      const response = await fetch("/api/email/token", {
        method: "GET",
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error("Unable to refresh email token");
      const data = await response.json();
      if (!data.token) throw new Error("Missing email token");
      emailSubscribeToken = data.token;
    };

    const sendSubscribe = async (email) =>
      fetch("/api/email/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          email,
          token: emailSubscribeToken,
        }),
      });

    const subscribe = async () => {
      const email = input.value.trim();
      if (!email) {
        setStatus("Enter an email address.");
        return;
      }

      setStatus("Subscribing...");
      acceptBtn.disabled = true;
      try {
        let response = await sendSubscribe(email);
        if (response.status === 403) {
          await refreshSubscribeToken();
          response = await sendSubscribe(email);
        }

        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          setStatus(data.error || "Unable to subscribe right now.");
          return;
        }

        localStorage.setItem("qb_email_subscribed", "true");
        localStorage.removeItem("qb_email_declined_at");
        setStatus(data.already_subscribed ? "You're already subscribed." : "Subscribed.");
        setTimeout(() => hideModal(), 900);
      } catch (_err) {
        setStatus("Unable to subscribe right now.");
      } finally {
        acceptBtn.disabled = false;
      }
    };

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      subscribe();
    });

    declineBtn.addEventListener("click", () => {
      localStorage.setItem("qb_email_declined_at", String(Date.now()));
      hideModal();
    });

    const schedulePrompt = (attempt = 0) => {
      if (!shouldPrompt()) return;
      if (hasVisiblePrompt()) {
        if (attempt < 6) setTimeout(() => schedulePrompt(attempt + 1), 1200);
        return;
      }
      showModal();
    };

    window.addEventListener("load", () => {
      setTimeout(() => schedulePrompt(), 1200);
    });
  };

  const setupOfflineNav = () => {
    const updateOfflineNav = () => {
      const isOffline = !navigator.onLine;
      document.querySelectorAll(".nav-item").forEach((item) => {
        const allowed = item.dataset.offlineAllowed === "true";
        item.style.display = isOffline && !allowed ? "none" : "";
      });
    };

    updateOfflineNav();
    window.addEventListener("online", updateOfflineNav);
    window.addEventListener("offline", updateOfflineNav);
  };

  setupInstallPrompt();
  setupPushPrompt();
  setupEmailSubscribePrompt();
  setupOfflineNav();
})();
