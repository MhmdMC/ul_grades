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
  const pipToggle = document.getElementById("pip-toggle");
  const volumeSlider = document.getElementById("pip-volume");
  const volumeLabel = document.getElementById("pip-volume-label");
  const testVolumeBtn = document.getElementById("test-volume");
  const volumeIosNote = document.getElementById("pip-volume-ios-note");

  let pipVolume = parseFloat(localStorage.getItem("pipVolume")) || 1;
  if (volumeSlider) volumeSlider.value = Math.round(pipVolume * 100);
  if (volumeLabel) volumeLabel.textContent = Math.round(pipVolume * 100) + "%";
  if (volumeSlider) {
    volumeSlider.addEventListener("input", () => {
      pipVolume = parseInt(volumeSlider.value) / 100;
      localStorage.setItem("pipVolume", pipVolume);
      if (volumeLabel) volumeLabel.textContent = Math.round(pipVolume * 100) + "%";
    });
  }
  if (testVolumeBtn) {
    testVolumeBtn.addEventListener("click", () => {
      ensureAudioUnlocked();
      playAlarm(2);
    });
  }

  // The dashboard renders courses as table rows (§6). The live-update hooks
  // live on the <tr>, not on a card.
  const rows = new Map();
  document
    .querySelectorAll(".grade-row")
    .forEach((row) => rows.set(row.dataset.courseKey, row));

  const socket = window.io
    ? window.io({ transports: ["polling", "websocket"] })
    : null;
  let audioContext = null;
  let monitoringStarted = false;

  // The connection badge has its own state classes. It must not borrow the
  // grade colors: those signal grades, and §2 gives each signal exactly one job.
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
    try {
      if (!audioContext)
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
      if (audioContext.state === "suspended") audioContext.resume();
    } catch (e) {
      console.warn("AudioContext error:", e);
    }
  }

  function beep(durationMs = 360, frequency = 880) {
    if (!audioContext) return;
    try {
      const oscillator = audioContext.createOscillator();
      const gain = audioContext.createGain();
      oscillator.type = "square";
      oscillator.frequency.value = frequency;
      oscillator.connect(gain);
      gain.connect(audioContext.destination);
      gain.gain.value = pipVolume * 0.75;
      oscillator.start();
      setTimeout(() => {
        try {
          oscillator.stop();
          oscillator.disconnect();
          gain.disconnect();
        } catch (e) { /* already stopped */ }
      }, durationMs);
    } catch (e) {
      console.warn("beep error:", e);
    }
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

  function gradeColor(value) {
    if (value === null || value === undefined) return null;
    const num = parseFloat(value);
    if (isNaN(num)) return null;
    const number = Math.max(0, Math.min(100, num));
    let hue, sat, light;
    if (number < 60) {
      const t = number / 60;
      hue = 0;
      sat = 72;
      light = 30 + 18 * t;
    } else {
      const t = (Math.min(number, 80) - 60) / 20;
      hue = 55 + 85 * t;
      sat = 75;
      light = 44 - 11 * t;
    }
    return `hsl(${Math.round(hue)} ${Math.round(sat)}% ${Math.round(light)}%)`;
  }

  function getInitialPiPData() {
    const name = studentName?.textContent?.trim();
    const avgEl = document.getElementById("stat-average");
    const rankEl = document.getElementById("stat-overall-rank");
    const avg = avgEl?.textContent?.trim();
    const rank = rankEl?.textContent?.trim();
    return {
      studentName: name && name !== "\u2014" ? name : "Dashboard",
      courseName: "Overall average",
      grade: avg && avg !== "\u2014" ? avg : null,
      rank: rank && rank !== "\u2014" ? rank : null,
      gradeColor: gradeColor(avg),
    };
  }

  class PiPManager {
    constructor(onStateChange) {
      this.video = null;
      this.canvas = null;
      this.ctx = null;
      this.stream = null;
      this.blobUrl = null;
      this.promptEl = null;
      this.active = false;
      this.supported = false;
      this.hasNewGrade = false;
      this.lastData = null;
      this._volumeTimer = null;
      this._pipGain = null;
      this._pipOsc = null;
      this.onStateChange = onStateChange || (() => {});
      this.checkSupport();
    }

    checkSupport() {
      if (document.pictureInPictureEnabled) {
        this.supported = true;
        return;
      }
      try {
        const v = document.createElement("video");
        if (
          v.webkitSupportsPresentationMode &&
          v.webkitSupportsPresentationMode("picture-in-picture")
        ) {
          this.supported = true;
        }
      } catch (e) {}
    }

    _ensureMuted() {
      if (!this.video) return;
      this.video.muted = true;
      this.video.defaultMuted = true;
      this.video.setAttribute("muted", "");
    }

    async updateVideoSrc(mode, courseName, noaudio = false) {
      if (!this.video) return;
      const v = Date.now();
      const params = new URLSearchParams({ mode, v });
      if (courseName) params.set("course_name", courseName);
      if (noaudio) params.set("noaudio", "1");
      if (this.blobUrl) {
        URL.revokeObjectURL(this.blobUrl);
        this.blobUrl = null;
      }
      this.video.src = `/api/pip-video?${params}`;
      this.video.loop = true;
      this.video.play().catch(() => {});
    }

    reRecord() {
      this.updateVideoSrc(
        this.hasNewGrade ? "grade" : "tracking",
        this.hasNewGrade ? this.lastData?.courseName : null,
      );
    }

    async start(data) {
      if (this.active) return true;
      this.cleanup();
      this.canvas = document.createElement("canvas");
      this.canvas.width = 320;
      this.canvas.height = 180;
      this.ctx = this.canvas.getContext("2d");
      this.lastData = data || this.lastData || getInitialPiPData();
      this.hasNewGrade = false;
      this.render(this.lastData);
      this.stream = this.canvas.captureStream(30);
      try {
        if (!audioContext)
          audioContext = new (window.AudioContext || window.webkitAudioContext)();
        const dst = audioContext.createMediaStreamDestination();
        this._pipOsc = audioContext.createOscillator();
        this._pipGain = audioContext.createGain();
        this._pipGain.gain.value = 0;
        this._pipOsc.connect(this._pipGain);
        this._pipGain.connect(dst);
        this._pipOsc.start();
        const audioTrack = dst.stream.getAudioTracks()[0];
        if (audioTrack) this.stream.addTrack(audioTrack);
      } catch (e) {}
      this.video = document.createElement("video");
      this.video.playsInline = true;
      this.video.setAttribute("playsinline", "");
      this.video.id = "pip-video";

      this.isSafari = !!(
        this.video.webkitSupportsPresentationMode || !document.pictureInPictureEnabled
      );

      if (this.isSafari) {
        this.canvas.style.cssText = "position:fixed;left:-9999px;top:-9999px";
        document.body.appendChild(this.canvas);
        this._ensureMuted();
        this.video.controls = true;
        this.video.poster = "/static/logo-ulfg-port.png";
        this.video.style.cssText =
          "position:fixed;bottom:90px;right:16px;width:200px;height:113px;border-radius:8px;z-index:50;box-shadow:0 4px 24px rgba(0,0,0,0.5);background:#000";
        document.body.appendChild(this.video);
        this.promptEl = document.createElement("div");
        this.promptEl.textContent = "Tap the \u25B6\uFE0F icon to enable Picture-in-Picture";
        this.promptEl.style.cssText =
          "position:fixed;bottom:calc(113px + 100px);right:16px;color:#94a3b8;font-size:12px;z-index:51;font-family:sans-serif;text-align:center;background:#0f172a;padding:6px 14px;border-radius:6px;border:1px solid #334155;white-space:nowrap";
        document.body.appendChild(this.promptEl);
        await new Promise((r) => requestAnimationFrame(r));
        this.updateVideoSrc("tracking");

        this.video.addEventListener("webkitpresentationmodechanged", () => {
          if (!this.video) return;
          const pip = this.video.webkitPresentationMode === "picture-in-picture";
          this.active = pip;
          this.onStateChange(pip);
        });
        this.video.addEventListener("click", () => {
          if (this.hasNewGrade) {
            this.hasNewGrade = false;
            if (this.video) this._ensureMuted();
            if (this._volumeTimer) {
              clearTimeout(this._volumeTimer);
              this._volumeTimer = null;
            }
            if (this._srcTimer) {
              clearTimeout(this._srcTimer);
              this._srcTimer = null;
            }
            this.updateVideoSrc("tracking");
          }
        });

        this.active = true;
        return true;
      }

      this._ensureMuted();
      this.video.style.cssText =
        "position:fixed;bottom:0;right:0;width:1px;height:1px;opacity:0.01;z-index:-1";
      document.body.appendChild(this.video);
      this.video.srcObject = this.stream;
      try {
        await this.video.play();
      } catch (e) {
        this.cleanup();
        return false;
      }
      try {
        if (this.video.requestPictureInPicture) {
          await this.video.requestPictureInPicture();
        }
      } catch (e) {
        this.cleanup();
        return false;
      }
      this.active = true;
      this.video.addEventListener("leavepictureinpicture", () => this.stop());
      this.video.addEventListener("click", () => {
        if (this.hasNewGrade) {
          try { window.focus(); } catch (e) {}
          this.hasNewGrade = false;
          if (this._pipGain) this._pipGain.gain.value = 0;
          if (this.ctx) this.render(this.lastData);
        }
      });
      return true;
    }

    update(data, isGradeChange = false) {
      if (!data) return;
      this.lastData = data;
      if (isGradeChange) this.hasNewGrade = true;
      if (isGradeChange) {
        if (this._volumeTimer) clearTimeout(this._volumeTimer);
        if (this._srcTimer) clearTimeout(this._srcTimer);
        if (this.isSafari && this.video) {
          this.video.muted = false;
          this.video.defaultMuted = false;
          this.video.removeAttribute("muted");
          this.updateVideoSrc("grade", this.lastData?.courseName);
          this._srcTimer = setTimeout(() => {
            this._srcTimer = null;
            if (this.video) this._ensureMuted();
            this.updateVideoSrc("grade", this.lastData?.courseName, true);
          }, 5500);
        }
        if (!this.isSafari && this._pipGain && this._pipOsc) {
          this._pipOsc.frequency.value = 440;
          this._pipOsc.type = "square";
          this._pipGain.gain.value = pipVolume * 0.75;
          this._volumeTimer = setTimeout(() => {
            this._volumeTimer = null;
            if (this._pipGain) this._pipGain.gain.value = 0;
          }, 5500);
        }
      }
      if (!this.active || !this.ctx) return;
      this.render(data);
    }

    render(data) {
      const ctx = this.ctx;
      const w = 320, h = 180;

      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, w, h);
      ctx.fillStyle = "#3d63a7";
      ctx.fillRect(12, 0, w - 24, 2);
      ctx.fillRect(12, h - 2, w - 24, 2);

      if (!this.hasNewGrade) {
        ctx.fillStyle = "#1e293b";
        ctx.font = 'bold 18px "IBM Plex Sans", sans-serif';
        ctx.textAlign = "center";
        ctx.fillText("Tracking...", w / 2, 70);

        ctx.fillStyle = "#64748b";
        ctx.font = "12px sans-serif";
        ctx.fillText("Monitoring active", w / 2, 100);
      } else {
        const courseName = data.courseName || "A course";
        ctx.fillStyle = "#0f1720";
        ctx.fillRect(0, 0, w, h);
        ctx.fillStyle = "#e2b105";
        ctx.fillRect(12, 0, w - 24, 2);
        ctx.fillRect(12, h - 2, w - 24, 2);

        ctx.fillStyle = "#ffffff";
        ctx.font = 'bold 16px "IBM Plex Sans", sans-serif';
        ctx.textAlign = "left";
        ctx.fillText(courseName, 16, 85);

        ctx.fillStyle = "#e2b105";
        ctx.font = '14px "IBM Plex Sans", sans-serif';
        ctx.fillText("just dropped!", 16, 114);

        ctx.fillStyle = "#94a3b8";
        ctx.font = "10px sans-serif";
        ctx.textAlign = "center";
        ctx.fillText("Tap to open dashboard", w / 2, h - 22);
      }
    }

    stop() {
      if (this.video) {
        if (document.pictureInPictureElement === this.video) {
          document.exitPictureInPicture();
        } else if (this.video.webkitPresentationMode === "picture-in-picture") {
          this.video.webkitSetPresentationMode("inline");
        }
      }
      this.cleanup();
    }

    cleanup() {
      if (this._volumeTimer) {
        clearTimeout(this._volumeTimer);
        this._volumeTimer = null;
      }
      if (this._srcTimer) {
        clearTimeout(this._srcTimer);
        this._srcTimer = null;
      }
      if (this.blobUrl) {
        URL.revokeObjectURL(this.blobUrl);
        this.blobUrl = null;
      }
      if (this.promptEl && this.promptEl.parentNode) {
        this.promptEl.parentNode.removeChild(this.promptEl);
      }
      if (this.canvas && this.canvas.parentNode) {
        this.canvas.parentNode.removeChild(this.canvas);
      }
      if (this._pipGain) {
        try { this._pipGain.disconnect(); } catch (e) {}
        this._pipGain = null;
      }
      if (this._pipOsc) {
        try { this._pipOsc.disconnect(); } catch (e) {}
        this._pipOsc = null;
      }
      if (this.stream) {
        this.stream.getTracks().forEach((t) => t.stop());
      }
      if (this.video && this.video.parentNode) {
        this.video.parentNode.removeChild(this.video);
      }
      this.active = false;
      this.promptEl = null;
      this.video = null;
      this.stream = null;
      this.canvas = null;
      this.ctx = null;
    }
  }

  const pipManager = new PiPManager((active) => {
    if (pipToggle) {
      pipToggle.textContent = active ? "Track while in other apps by PIP \u25CF" : "Track while in other apps by PIP";
      pipToggle.classList.toggle("active", active);
    }
  });

  if (volumeIosNote && pipManager.supported) {
    const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    if (isIOS) volumeIosNote.style.display = "";
  }

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && pipManager.active) pipManager.stop();
  });

  if (pipToggle) {
    pipToggle.style.display = pipManager.supported ? "" : "none";
    pipToggle.addEventListener("click", async () => {
      if (pipManager.active) {
        pipManager.stop();
      } else if (await pipManager.start()) {
        pipManager.onStateChange(true);
      }
    });
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
    ensureAudioUnlocked();
    overlay?.classList.add("hidden");
    setStatus("Connected", "good");
    socket?.emit("join_dashboard");
    socket?.emit("request_refresh");
    refreshDashboard();
  }

  function testGradeSound() {
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
    toast("Alarm sent", "Playing alarm for all connected clients.", "info");
    playAlarm(3);
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
    socket.on("dashboard_payload", (payload) => {
      updateDashboard(payload);
      pipManager.update({
        studentName: payload.student_name,
        courseName: payload.student_name || "Dashboard",
        grade: payload.average,
        rank: payload.overall_rank,
        gradeColor: gradeColor(payload.average),
      });
    });
    socket.on("grade_change", (payload) => {
      if (!payload.is_test) updateDashboard(payload);
      const firstChange = payload?.changes?.[0];
      if (firstChange?.course?.key && !payload.is_test)
        highlightCourse(
          firstChange.course.key,
          firstChange.type === "removed_course" ? "removed" : "changed",
        );
      if (payload.is_test) {
        const key = firstChange?.course?.key;
        const courseName = firstChange?.course?.course_name || "Simulated Grade";
        const grade = firstChange?.course?.partial;
        const rank = firstChange?.course?.partial_rank;
        const color = firstChange?.course?.grade_color;
        const tbody = document.querySelector(".table tbody");
        if (tbody && key) {
          const row = document.createElement("tr");
          row.className = "grade-row";
          row.dataset.courseKey = key;
          row.id = `test-grade-${key}`;
          row.innerHTML = `
            <td>${courseName}</td>
            <td>TEST</td>
            <td class="num">&mdash;</td>
            <td class="num"${color ? ` style="color:${color}"` : ""}>${grade || "&mdash;"}</td>
            <td class="num">${rank || "&mdash;"}</td>
            <td class="num is-missing">&mdash;</td>
            <td class="num is-missing">&mdash;</td>
            <td class="col-spaced is-missing">&mdash;</td>`;
          tbody.prepend(row);
          rows.set(key, row);
        }
      }
      toast(
        "New grade detected",
        payload?.changes?.[0]
          ? `${payload.changes[0].course.course_name || payload.changes[0].course.course_code || "Course"} changed`
          : "Grade update detected",
        "success",
      );
      playAlarm(5);
      if (firstChange?.course) {
        const course = firstChange.course;
        pipManager.update({
          studentName: payload.student_name,
          courseName: course.course_name || course.course_code,
          grade: course.partial ?? course.final ?? course.final_grade,
          rank: course.partial_rank ?? course.final_rank,
          gradeColor: gradeColor(course.partial ?? course.final ?? course.final_grade),
        }, true);
      }
    });
    socket.on("remove_test_grade", (data) => {
      const key = data?.key;
      if (!key) return;
      const row = document.getElementById(`test-grade-${key}`);
      if (row) {
        rows.delete(key);
        row.remove();
      }
    });
  }
})();
