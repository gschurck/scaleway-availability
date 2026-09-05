(() => {
  const switchInput = () => document.querySelector('.availability-switch input');

  const syncSelection = (includeLow) => {
    const toggle = switchInput();
    if (!toggle) return;
    toggle.checked = includeLow;
    const filter = document.querySelector('#ranking-filters [name="include_low"]');
    if (filter) filter.value = String(includeLow);
    document.querySelectorAll('[data-availability-link]').forEach((link) => {
      const url = new URL(link.href);
      url.searchParams.set('include_low', includeLow);
      link.href = url.href;
    });
  };

  document.addEventListener('htmx:configRequest', (event) => {
    const form = event.detail.elt;
    if (!form.matches('.availability-setting, #ranking-filters')) return;
    if (form.matches('.availability-setting')) {
      const filters = document.querySelector('#ranking-filters');
      const parameters = filters
        ? new FormData(filters)
        : new URLSearchParams(window.location.search);
      // Read live filters or the latest date-range URL after partial updates.
      Object.keys(event.detail.parameters).forEach((key) => delete event.detail.parameters[key]);
      parameters.forEach((value, key) => { event.detail.parameters[key] = value; });
    }
    event.detail.parameters.include_low = String(switchInput().checked);
  });

  document.addEventListener('htmx:afterRequest', (event) => {
    if (!event.detail.elt.matches('.availability-setting, #ranking-filters')) return;
    const value = event.detail.successful
      ? event.detail.requestConfig.parameters.include_low
      : new URLSearchParams(window.location.search).get('include_low');
    syncSelection(value === 'true');
  });

  document.addEventListener('htmx:historyRestore', () => {
    syncSelection(new URLSearchParams(window.location.search).get('include_low') === 'true');
  });
})();
