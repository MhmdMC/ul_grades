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
  // The dashboard renders courses as table rows (§6). The live-update hooks
  // live on the <tr>, not on a card.
  const rows = new Map();
  document
    .querySelectorAll(".grade-row")
    .forEach((row) => rows.set(row.dataset.courseKey, row));

  const socket = window.io
    ? window.io({ transports: ["websocket", "polling"] })
    : null;
  let audioContext = null;
  let monitoringStarted = false;

  // The connection badge has its own state classes. It must not borrow
  // value-green / -orange / -red: those are grade signals, and §2 gives each
  // signal exactly one job.
  const CONN_CLASSES = ["badge-muted", "conn-online", "conn-idle", "conn-offline"];
  const CONN_STATE = {
    good: "conn-online",
    warn: "conn-offline",
    bad: "conn-offline",
  };

  function setStatus(text, state = "normal") {
    if (!connectionStatus) return;
    connectionStatus.textContent = text;
    connectionStatus.classList.remove(...CONN_CLASSES);
    connectionStatus.classList.add(CONN_STATE[state] || "conn-idle");
  }

  function ensureAudioUnlocked() {
    if (!window.AudioContext && !window.webkitAudioContext) return;
    if (!audioContext)
      audioContext = new (window.AudioContext || window.webkitAudioContext)();
    if (audioContext.state === "suspended") audioContext.resume();
  }

  function beep(durationMs = 360, frequency = 880) {
    if (!audioContext) return;
    const oscillator = audioContext.createOscillator();
    const gain = audioContext.createGain();
    oscillator.type = "square";
    oscillator.frequency.value = frequency;
    oscillator.connect(gain);
    gain.connect(audioContext.destination);
    gain.gain.value = 1;
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
      beep(360, 880);
      if (Date.now() - started >= seconds * 1000) clearInterval(timer);
    }, 350);
    beep(360, 660);
  }

  const TOAST_KINDS = new Set(["success", "warning", "error", "info"]);

  function toast(title, message, kind = "info") {
    if (!toastRoot) return;
    const variant = TOAST_KINDS.has(kind) ? kind : "info";

    const element = document.createElement("div");
    element.className = `toast toast-${variant}`;

    const titleNode = document.createElement("div");
    titleNode.className = "toast-title";
    titleNode.textContent = title;

    const messageNode = document.createElement("div");
    messageNode.className = "toast-message";
    messageNode.textContent = message;

    element.append(titleNode, messageNode);
    toastRoot.appendChild(element);

    setTimeout(() => {
      element.style.opacity = "0";
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
    const row = rows.get(courseKey);
    if (!row) return;
    row.classList.remove("is-changed", "is-removed");
    void row.offsetWidth; // restart the flash if the row changes again
    if (kind === "removed") row.classList.add("is-removed");
    else row.classList.add("is-changed");
    setTimeout(() => row.classList.remove("is-changed"), 4500);
  }

  function setStat(id, value) {
    const element = document.getElementById(id);
    if (!element) return;
    const missing = value === null || value === undefined;
    element.textContent = missing ? "—" : value;
    element.classList.toggle("is-missing", missing);
  }

  function updateDashboard(payload) {
    if (!payload) return;
    if (studentName && payload.student_name)
      studentName.textContent = payload.student_name;
    if (lastUpdate && payload.updated_at)
      lastUpdate.textContent = payload.updated_at;
    setStat("stat-average", payload.average);
    setStat("stat-overall-rank", payload.overall_rank);
    setStat("stat-final-average", payload.final_average);
    setStat("stat-final-rank", payload.final_rank);
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
    // Unlock first: the alarm comes back to this browser as a broadcast like
    // any other client's, and audio will not play without a user gesture.
    ensureAudioUnlocked();

    if (!socket) {
      toast(
        "Not connected",
        "No live connection, so the alarm was played here only.",
        "warning",
      );
      playAlarm(2);
      return;
    }

    if (!window.confirm("Play the alarm for every connected user right now?"))
      return;

    socket.emit("admin_test_alarm");
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
    socket.on("play_alarm", (data) => {
      toast(
        data?.title || "Sound test",
        data?.message || "An admin triggered the grade-change alarm.",
        "info",
      );
      playAlarm(data?.seconds || 3);
    });
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
