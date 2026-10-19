// decomp.fun: Add a project form. After submitting, the page watches for the check's result
// (published to /submissions.json when the check run deploys) and shows it in place.
(() => {
  const signedOut = document.getElementById("signed-out");
  const signedIn = document.getElementById("signed-in");
  const who = document.getElementById("who");
  const form = document.getElementById("submit-form");
  const result = document.getElementById("submit-result");
  const system = document.getElementById("system");
  const gameRow = document.getElementById("game-row");
  const PENDING = "decomp-fun-pending";

  const show = (text, kind) => {
    result.innerHTML = "";
    if (typeof text === "string") result.textContent = text;
    else result.append(text);
    result.className = `notice${kind ? ` ${kind}` : ""}`;
    result.hidden = false;
  };

  fetch("/api/me", { credentials: "same-origin" })
    .then((r) => r.json())
    .then((u) => {
      if (u && u.login) {
        who.textContent = u.login;
        signedIn.hidden = false;
        signedOut.hidden = true;
      }
    })
    .catch(() => {});

  if (new URL(location.href).searchParams.get("error")) show("GitHub sign-in didn't complete. Try again.", "error");

  system?.addEventListener("change", () => {
    gameRow.hidden = !system.value;
  });

  // ---------------------------------------------------------------- waiting for the result
  const render = (entry) => {
    const box = document.createElement("div");
    const head = document.createElement("strong");
    head.textContent = { listed: "Accepted", contesting: "Contesting", already: "Already listed" }[entry.status] || "Not accepted";
    box.append(head, document.createTextNode(` · ${entry.repo}${entry.game ? ` (${entry.game})` : ""}`));
    if (entry.reasons.length) {
      const ul = document.createElement("ul");
      for (const r of entry.reasons) {
        const li = document.createElement("li");
        li.textContent = r;
        ul.append(li);
      }
      box.append(ul);
    }
    if (entry.url) {
      const a = document.createElement("a");
      a.href = entry.url;
      a.textContent = "Open its page →";
      box.append(a);
    }
    return box;
  };

  const watch = (repo, since) => {
    const started = Date.now();
    const tick = async () => {
      try {
        const list = await (await fetch(`/submissions.json?t=${Date.now()}`, { cache: "no-store" })).json();
        const match = list.find((x) => x.repo.toLowerCase() === repo.toLowerCase());
        const fresh = match && (match.submitted_at >= since ? match
          : match.last_check && match.last_check.at >= since ? { ...match, ...match.last_check } : null);
        if (fresh) {
          sessionStorage.removeItem(PENDING);
          show(render(fresh), fresh.status === "listed" || fresh.status === "already" ? "" : fresh.status === "contesting" ? "warn" : "error");
          return;
        }
      } catch {}
      if (Date.now() - started > 20 * 60e3) {
        sessionStorage.removeItem(PENDING);
        show("The check is taking longer than usual. The result will appear in the list below when it's done.");
        return;
      }
      setTimeout(tick, 15e3);
    };
    tick();
  };

  try {
    const pending = JSON.parse(sessionStorage.getItem(PENDING) || "null");
    if (pending) {
      show(`Checking ${pending.repo}… this page updates by itself when the result is ready.`);
      watch(pending.repo, pending.since);
    }
  } catch {}

  form?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const button = form.querySelector("button[type=submit]");
    button.setAttribute("aria-busy", "true");
    button.disabled = true;
    const since = new Date(Date.now() - 60e3).toISOString().slice(0, 19);
    try {
      const res = await fetch("/api/submit", {
        method: "POST",
        credentials: "same-origin",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ repo: form.repo.value, system: form.system.value, game: form.game.value }),
      });
      const data = await res.json();
      if (data.ok) {
        const repo = data.repo || form.repo.value.replace(/^(https?:\/\/)?(www\.)?github\.com\//i, "").replace(/\.git$|\/+$/g, "");
        try {
          sessionStorage.setItem(PENDING, JSON.stringify({ repo, since }));
        } catch {}
        show(`Submitted ${repo}. Checking now (about 2–5 minutes); this page updates by itself.`);
        form.reset();
        gameRow.hidden = true;
        watch(repo, since);
      } else {
        show(data.message, "error");
      }
    } catch {
      show("Couldn't reach the server. Try again.", "error");
    }
    button.removeAttribute("aria-busy");
    button.disabled = false;
  });
})();
