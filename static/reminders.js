(function () {
  const body = document.body;
  const userId = body && body.dataset.userId;
  const enabled = body && body.dataset.reminderEnabled === "1";
  const reminderTime = body && body.dataset.reminderTime;
  const status = document.getElementById("reminder-permission-status");
  const permissionButton = document.getElementById("enable-reminder-notifications");

  function setStatus(message) {
    if (status) status.textContent = message;
  }

  if (permissionButton) {
    permissionButton.addEventListener("click", async () => {
      if (!("Notification" in window)) {
        setStatus("This browser does not support notifications.");
        return;
      }
      try {
        const permission = await Notification.requestPermission();
        setStatus(permission === "granted"
          ? "Browser notifications are allowed. Save your reminder preference to turn it on."
          : "Notifications were not allowed. You can change this in your browser settings.");
      } catch (_) {
        setStatus("Could not request notification permission in this browser.");
      }
    });
  }

  if (!userId || !enabled || !reminderTime || !("Notification" in window)) return;

  const lastKey = `nutritrack-reminder-last:${userId}`;
  const checkReminder = async () => {
    if (Notification.permission !== "granted") {
      if (status) setStatus("Allow browser notifications to receive this reminder.");
      return;
    }
    const [targetHour, targetMinute] = reminderTime.split(":").map(Number);
    if (!Number.isInteger(targetHour) || !Number.isInteger(targetMinute)) return;
    const now = new Date();
    const targetMinutes = targetHour * 60 + targetMinute;
    if (now.getHours() * 60 + now.getMinutes() < targetMinutes) return;

    const localDate = [
      now.getFullYear(),
      String(now.getMonth() + 1).padStart(2, "0"),
      String(now.getDate()).padStart(2, "0")
    ].join("-");
    try {
      if (localStorage.getItem(lastKey) === localDate) return;
      const options = {
        body: "Take a moment to record your meals for today.",
        icon: "/static/nutritrack-icon.svg",
        tag: `nutritrack-food-log-${localDate}`
      };
      if (navigator.serviceWorker) {
        const registration = await navigator.serviceWorker.ready;
        await registration.showNotification("Time to update your food log", options);
      } else {
        new Notification("Time to update your food log", options);
      }
      localStorage.setItem(lastKey, localDate);
    } catch (_) {
      setStatus("Could not show a reminder. Check browser notification settings.");
    }
  };

  checkReminder();
  window.setInterval(checkReminder, 30_000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) checkReminder();
  });
})();