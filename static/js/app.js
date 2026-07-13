(() => {
  const csrfToken =
    document.querySelector('meta[name="csrf-token"]')?.content || "";
  const overlay = document.getElementById("enter-overlay");
  const enterButton = document.getElementById("enter-monitoring");
  const toastRoot = document.getElementById("toast-root");
  const connectionStatus = document.getElementById("connection-status");
  const lastUpdate = document.getElementById("last-update");
  const studentName = document.getElementById("student-name");
  const testGradeSoundButton = document.getElementById("test-grade-sound");
  const copyCookieButtons = document.querySelectorAll(".copy-cookie-button");
  const cards = new Map();
  document
    .querySelectorAll(".grade-card")
    .forEach((card) => cards.set(card.dataset.courseKey, card));

  const socket = window.io
    ? window.io({ transports: ["websocket", "polling"] })
    : null;
  let audioContext = null;
  let monitoringStarted = false;

  function setStatus(text, state = "normal") {
    if (!connectionStatus) return;
    connectionStatus.textContent = text;
    connectionStatus.classList.remove(
      "text-emerald-300",
      "text-amber-300",
      "text-rose-300",
    );
    if (state === "good") connectionStatus.classList.add("text-emerald-300");
    else if (state === "warn") connectionStatus.classList.add("text-amber-300");
    else if (state === "bad") connectionStatus.classList.add("text-rose-300");
  }

  function ensureAudioUnlocked() {
    if (!window.AudioContext && !window.webkitAudioContext) return;
    if (!audioContext)
      audioContext = new (window.AudioContext || window.webkitAudioContext)();
    if (audioContext.state === "suspended") audioContext.resume();
  }

  function beep(durationMs = 180, frequency = 880) {
    if (!audioContext) return;
    const oscillator = audioContext.createOscillator();
    const gain = audioContext.createGain();
    oscillator.type = "square";
    oscillator.frequency.value = frequency;
    oscillator.connect(gain);
    gain.connect(audioContext.destination);
    gain.gain.value = 0.05;
    oscillator.start();
    setTimeout(() => {
      oscillator.stop();
      oscillator.disconnect();
      gain.disconnect();
    }, durationMs);
  }

  function playAlarm(seconds = 5) {
    ensureAudioUnlocked();
    const started = Date.now();
    const timer = setInterval(() => {
      beep(180, 880);
      if (Date.now() - started >= seconds * 1000) clearInterval(timer);
    }, 350);
    beep(180, 660);
  }

  function toast(title, message, kind = "info") {
    if (!toastRoot) return;
    const element = document.createElement("div");
    element.className = `rounded-2xl border px-4 py-3 shadow-glow backdrop-blur-xl transition-all duration-300 ${kind === "success" ? "border-emerald-400/30 bg-emerald-400/10" : kind === "warning" ? "border-amber-400/30 bg-amber-400/10" : kind === "error" ? "border-rose-400/30 bg-rose-400/10" : "border-white/10 bg-slate-900/80"}`;
    element.innerHTML = `<div class="text-sm font-semibold">${title}</div><div class="mt-1 text-sm text-slate-200/90">${message}</div>`;
    toastRoot.appendChild(element);
    setTimeout(() => {
      element.style.opacity = "0";
      element.style.transform = "translateY(8px)";
      setTimeout(() => element.remove(), 300);
    }, 4000);
  }

  async function copyText(text) {
    if (!text) return false;
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "true");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand("copy");
    textarea.remove();
    return copied;
  }

  function highlightCourse(courseKey, kind = "changed") {
    const card = cards.get(courseKey);
    if (!card) return;
    card.classList.remove("is-changed", "is-removed");
    void card.offsetWidth;
    if (kind === "removed") card.classList.add("is-removed");
    else card.classList.add("is-changed");
    setTimeout(() => card.classList.remove("is-changed"), 4500);
  }

  function updateDashboard(payload) {
    if (!payload) return;
    if (studentName && payload.student_name)
      studentName.textContent = payload.student_name;
    if (lastUpdate && payload.updated_at)
      lastUpdate.textContent = payload.updated_at;
    if (payload.courses) {
      payload.courses.forEach((course) => {
        highlightCourse(course.key, "changed");
      });
    }
  }

  async function refreshDashboard() {
    try {
      const response = await fetch("/api/dashboard", {
        headers: { "X-CSRFToken": csrfToken },
      });
      if (!response.ok) return;
      const payload = await response.json();
      updateDashboard(payload);
    } catch (error) {
      console.error(error);
    }
  }

  function unlockMonitoring() {
    if (monitoringStarted) return;
    monitoringStarted = true;
    try {
      localStorage.setItem("ul_grade_monitor_unlocked", "1");
    } catch (error) {
      console.warn(error);
    }
    ensureAudioUnlocked();
    overlay?.classList.add("hidden");
    setStatus("Connected", "good");
    socket?.emit("join_dashboard");
    socket?.emit("request_refresh");
    refreshDashboard();
  }

  function testGradeSound() {
    ensureAudioUnlocked();
    toast(
      "Sound test",
      "This is the same alert used for live grade changes.",
      "info",
    );
    playAlarm(2);
  }

  if (overlay && localStorage.getItem("ul_grade_monitor_unlocked") === "1") {
    overlay.classList.add("hidden");
    monitoringStarted = true;
  }

  enterButton?.addEventListener("click", unlockMonitoring);
  testGradeSoundButton?.addEventListener("click", testGradeSound);
  copyCookieButtons.forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        const copied = await copyText(button.dataset.cookieValue || "");
        toast(
          copied ? "Cookie copied" : "Copy failed",
          copied
            ? "The UL cookie is now in your clipboard."
            : "Your browser blocked clipboard access.",
          copied ? "success" : "warning",
        );
      } catch (error) {
        console.error(error);
        toast(
          "Copy failed",
          "Your browser blocked clipboard access.",
          "warning",
        );
      }
    });
  });
  overlay?.addEventListener("click", (event) => {
    if (event.target === overlay) unlockMonitoring();
  });

  if (socket) {
    socket.on("connect", () => {
      setStatus("Connected", "good");
      if (monitoringStarted) socket.emit("join_dashboard");
    });

    socket.on("disconnect", () => setStatus("Disconnected", "warn"));
    socket.on("connection_state", (data) => {
      if (data?.message) setStatus(data.message, "good");
    });
    socket.on("status_update", (data) => {
      if (data?.message)
        toast("Monitoring update", data.message, data.level || "info");
      if (lastUpdate && data?.timestamp)
        lastUpdate.textContent = data.timestamp;
    });
    socket.on("cookie_required", (data) => {
      toast(
        "Cookie required",
        data?.message || "Please update your UL cookie.",
        "warning",
      );
      if (data?.message) setStatus(data.message, "warn");
    });
    socket.on("toast", (data) =>
      toast(data.title || "Update", data.message || "", data.kind || "info"),
    );
    socket.on("dashboard_payload", updateDashboard);
    socket.on("grade_change", (payload) => {
      updateDashboard(payload);
      const firstChange = payload?.changes?.[0];
      if (firstChange?.course?.key)
        highlightCourse(
          firstChange.course.key,
          firstChange.type === "removed_course" ? "removed" : "changed",
        );
      toast(
        "New grade detected",
        payload?.changes?.[0]
          ? `${payload.changes[0].course.course_name || payload.changes[0].course.course_code || "Course"} changed`
          : "Grade update detected",
        "success",
      );
      playAlarm(5);
    });
  }
})();
