(() => {
  const storageKey = "metal-availability-theme";
  const root = document.documentElement;
  const systemPreference = window.matchMedia("(prefers-color-scheme: dark)");

  const storedTheme = () => {
    try {
      const value = window.localStorage.getItem(storageKey);
      return value === "light" || value === "dark" ? value : null;
    } catch {
      return null;
    }
  };

  const applyTheme = (theme) => {
    root.dataset.theme = theme;
    const toggle = document.querySelector("[data-theme-toggle]");
    if (!toggle) return;
    const isDark = theme === "dark";
    const label = `Switch to ${isDark ? "light" : "dark"} theme`;
    toggle.setAttribute("aria-label", label);
    toggle.setAttribute("title", label);
    toggle.setAttribute("aria-pressed", String(isDark));
  };

  applyTheme(storedTheme() || (systemPreference.matches ? "dark" : "light"));

  document.addEventListener("DOMContentLoaded", () => applyTheme(root.dataset.theme));
  document.addEventListener("htmx:afterSwap", () => applyTheme(root.dataset.theme));
  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    if (!event.target.closest("[data-theme-toggle]")) return;
    const theme = root.dataset.theme === "dark" ? "light" : "dark";
    try {
      window.localStorage.setItem(storageKey, theme);
    } catch {
      // The selected theme still applies for the current page.
    }
    applyTheme(theme);
  });

  systemPreference.addEventListener("change", (event) => {
    if (!storedTheme()) applyTheme(event.matches ? "dark" : "light");
  });
})();
