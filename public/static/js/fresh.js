// decomp.fun: on a project page, ask whether GitHub has newer progress than this page shows.
// If so, the site refreshes just this project and this page reloads itself when it's live.
(() => {
  const el = document.getElementById("fresh");
  if (!el) return;
  const repo = el.dataset.repo;
  const sha = el.dataset.sha;

  const note = (text) => {
    el.className = "notice";
    el.innerHTML = `<small>${text}</small>`;
    el.hidden = false;
  };

  const waitForNewVersion = () => {
    const started = Date.now();
    const tick = async () => {
      try {
        const fresh = await (await fetch(`/fresh.json?t=${Date.now()}`, { cache: "no-store" })).json();
        if (fresh[repo] && fresh[repo].sha && fresh[repo].sha !== sha) {
          location.reload();
          return;
        }
      } catch {}
      if (Date.now() - started < 10 * 60e3) setTimeout(tick, 20e3);
      else note("New progress is still being processed. Check back in a few minutes.");
    };
    setTimeout(tick, 20e3);
  };

  // The Worker only reads `repo`; `sha` keeps a page built from a newer commit from reusing the
  // browser-cached answer (the response is cacheable for a minute).
  fetch(`/api/fresh?repo=${encodeURIComponent(repo)}&sha=${encodeURIComponent(sha)}`)
    .then((r) => r.json())
    .then((d) => {
      if (d.status === "updating") {
        note("New progress found on GitHub. Updating this page now (about 2 minutes); it reloads by itself.");
        waitForNewVersion();
      } else if (d.status === "busy") {
        note("New progress found on GitHub. It will appear with the next hourly update.");
      }
    })
    .catch(() => {});
})();
