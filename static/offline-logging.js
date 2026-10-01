(function () {
  const USER_KEY = "nutritrack-active-user";
  const body = document.body;
  const userIdFromPage = body && body.dataset.userId;
  const deletedUserId = body && body.dataset.offlineCleanupUserId;
  const offlinePage = body && body.dataset.offlinePage === "1";
  if (userIdFromPage) {
    try {
      localStorage.setItem(USER_KEY, userIdFromPage);
    } catch (_) {}
  } else if (!offlinePage) {
    try {
      localStorage.removeItem(USER_KEY);
    } catch (_) {}
  }
  if (deletedUserId) {
    try {
      ["foods", "queue"].forEach((type) => {
        localStorage.removeItem(`nutritrack-${type}:${deletedUserId}`);
      });
      localStorage.removeItem(`nutritrack-reminder-last:${deletedUserId}`);
      if (localStorage.getItem(USER_KEY) === deletedUserId) {
        localStorage.removeItem(USER_KEY);
      }
    } catch (_) {}
  }

  let userId = userIdFromPage;
  try {
    userId = userId || localStorage.getItem(USER_KEY) || "";
  } catch (_) {
    userId = userId || "";
  }

  const storageKey = (type) => `nutritrack-${type}:${userId}`;
  const getStoredList = (key) => {
    try {
      const value = JSON.parse(localStorage.getItem(key) || "[]");
      return Array.isArray(value) ? value : [];
    } catch (_) {
      return [];
    }
  };
  const setStoredList = (key, value) => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
      return true;
    } catch (_) {
      return false;
    }
  };
  const localDate = () => {
    const now = new Date();
    const offset = now.getTimezoneOffset() * 60000;
    return new Date(now.getTime() - offset).toISOString().slice(0, 10);
  };
  const newClientId = () => {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (char) => {
      const value = Math.floor(Math.random() * 16);
      return (char === "x" ? value : (value & 0x3) | 0x8).toString(16);
    });
  };
  const statusNode = document.getElementById("offline-status") || document.getElementById("offline-log-status");

  function setStatus(message) {
    if (statusNode) statusNode.textContent = message;
  }

  function cacheDashboardFoods() {
    if (!userId) return;
    const select = document.querySelector("select[data-offline-foods]");
    if (!select) return;
    const foods = Array.from(select.options)
      .filter((option) => option.value)
      .map((option) => ({ id: option.value, name: option.textContent.trim() }));
    setStoredList(storageKey("foods"), foods);
  }

  function renderOfflineFoods() {
    const select = document.getElementById("offline-food");
    const submit = document.getElementById("offline-save");
    if (!select || !userId) {
      if (select) select.innerHTML = '<option value="">Sign in online first</option>';
      if (submit) submit.disabled = true;
      setStatus("Sign in while connected to prepare this device for offline logging.");
      return;
    }
    const foods = getStoredList(storageKey("foods"));
    select.replaceChildren();
    if (!foods.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "No foods saved yet";
      select.append(option);
      if (submit) submit.disabled = true;
      setStatus("Open the dashboard while online once to save your food list on this device.");
      return;
    }
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Select food…";
    select.append(placeholder);
    foods.forEach((food) => {
      const option = document.createElement("option");
      option.value = food.id;
      option.textContent = food.name;
      select.append(option);
    });
    if (submit) submit.disabled = false;
  }

  function renderQueue() {
    const list = document.getElementById("offline-pending");
    if (!list || !userId) return;
    const queue = getStoredList(storageKey("queue"));
    list.replaceChildren();
    if (!queue.length) {
      const item = document.createElement("li");
      item.textContent = "No meals waiting to sync.";
      list.append(item);
      return;
    }
    queue.forEach((entry) => {
      const item = document.createElement("li");
      const foods = getStoredList(storageKey("foods"));
      const food = foods.find((candidate) => String(candidate.id) === String(entry.food_id));
      item.textContent = `${entry.date} · ${food ? food.name : "Food"} · ${entry.grams}g · ${entry.meal_type}`;
      list.append(item);
    });
  }

  function queueEntry(entry) {
    if (!userId) {
      setStatus("Sign in while connected before saving meals offline.");
      return false;
    }
    const queue = getStoredList(storageKey("queue"));
    queue.push({ client_id: newClientId(), ...entry });
    if (!setStoredList(storageKey("queue"), queue)) {
      setStatus("This browser could not save the meal locally. Check device storage and try again.");
      return false;
    }
    renderQueue();
    return true;
  }

  let syncing = false;
  async function syncQueue() {
    if (!userId || !navigator.onLine || syncing) return;
    const queue = getStoredList(storageKey("queue"));
    if (!queue.length) return;
    syncing = true;
    setStatus("Connection restored. Syncing saved meals…");
    try {
      const response = await fetch("/sync-food-logs", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entries: queue })
      });
      const contentType = response.headers.get("content-type") || "";
      if (!response.ok || !contentType.includes("application/json")) {
        setStatus("Meals are still saved on this device. Sign in and try syncing again.");
        return;
      }
      const result = await response.json();
      const accepted = new Set(result.accepted_ids || []);
      const remaining = getStoredList(storageKey("queue"))
        .filter((entry) => !accepted.has(entry.client_id));
      setStoredList(storageKey("queue"), remaining);
      renderQueue();
      setStatus(remaining.length
        ? "Some meals remain on this device and will be retried."
        : "Your saved meals have been synced.");
    } catch (_) {
      setStatus("Could not sync yet. Your meals remain saved on this device.");
    } finally {
      syncing = false;
    }
  }

  function attachDashboardForm() {
    const form = document.querySelector('form[action="/log-food"]');
    if (!form) return;
    form.addEventListener("submit", (event) => {
      if (navigator.onLine) return;
      event.preventDefault();
      const data = new FormData(form);
      const queued = queueEntry({
        food_id: String(data.get("food_id") || ""),
        grams: Number(data.get("grams")),
        meal_type: String(data.get("meal_type") || "Snack"),
        date: localDate()
      });
      if (queued) {
        form.reset();
        setStatus("Saved on this device. It will sync when you reconnect.");
      }
    });
  }

  function attachOfflineForm() {
    const form = document.getElementById("offline-queue-form");
    if (!form) return;
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const data = new FormData(form);
      const grams = Number(data.get("grams"));
      if (!data.get("food_id") || !Number.isFinite(grams) || grams < 1 || grams > 2000) {
        setStatus("Choose a food and an amount from 1 to 2,000 grams.");
        return;
      }
      if (queueEntry({
        food_id: String(data.get("food_id")),
        grams,
        meal_type: String(data.get("meal_type") || "Snack"),
        date: localDate()
      })) {
        setStatus("Meal saved on this device. It will sync when you reconnect.");
        form.reset();
        renderOfflineFoods();
      }
    });
  }

  cacheDashboardFoods();
  renderOfflineFoods();
  renderQueue();
  attachDashboardForm();
  attachOfflineForm();
  window.addEventListener("online", syncQueue);
  if (navigator.onLine) window.addEventListener("load", syncQueue, { once: true });
})();