// Shared helpers for the سَند app

async function apiFetch(url, options = {}) {
  const opts = {
    method: "GET",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...options,
  };
  if (opts.body && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
  }
  try {
    const res = await fetch(url, opts);
    let data = {};
    try {
      data = await res.json();
    } catch (e) {
      data = {};
    }
    return { ok: res.ok, status: res.status, data };
  } catch (err) {
    console.error("Network error calling", url, err);
    return { ok: false, status: 0, data: { success: false, message: "تعذر الاتصال بالخادم" } };
  }
}

function showToast(message, type = "") {
  let toast = document.querySelector(".toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.className = "toast";
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.className = "toast show" + (type ? " " + type : "");
  clearTimeout(toast._hideTimer);
  toast._hideTimer = setTimeout(() => {
    toast.classList.remove("show");
  }, 3200);
}

function logout() {
  apiFetch("/api/logout", { method: "POST" }).finally(() => {
    window.location.href = "/";
  });
}
