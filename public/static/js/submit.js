// decomp.fun: Add a project form.
(() => {
  const signedOut = document.getElementById("signed-out");
  const signedIn = document.getElementById("signed-in");
  const who = document.getElementById("who");
  const form = document.getElementById("submit-form");
  const result = document.getElementById("submit-result");
  const system = document.getElementById("system");
  const gameRow = document.getElementById("game-row");

  fetch("/api/me", { credentials: "same-origin" })
    .then((r) => (r.ok ? r.json() : null))
    .then((u) => {
      if (u && u.login) {
        who.textContent = u.login;
        signedIn.hidden = false;
        signedOut.hidden = true;
      }
    })
    .catch(() => {});

  const error = new URL(location.href).searchParams.get("error");
  if (error && result) {
    result.hidden = false;
    result.textContent = "GitHub sign-in didn't complete. Try again.";
  }

  system?.addEventListener("change", () => {
    gameRow.hidden = !system.value;
  });

  form?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const button = form.querySelector("button[type=submit]");
    button.setAttribute("aria-busy", "true");
    button.disabled = true;
    try {
      const res = await fetch("/api/submit", {
        method: "POST",
        credentials: "same-origin",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          repo: form.repo.value,
          system: form.system.value,
          game: form.game.value,
        }),
      });
      const data = await res.json();
      result.textContent = data.message;
      result.className = data.ok ? "notice" : "notice error";
      if (data.ok) form.reset();
    } catch {
      result.textContent = "Couldn't reach the server. Try again.";
      result.className = "notice error";
    }
    result.hidden = false;
    button.removeAttribute("aria-busy");
    button.disabled = false;
  });
})();
