(function () {
  const STORAGE_KEY = 'slumpguard_api_base';

  function normalizeBase(value) {
    const text = String(value || '').trim();
    if (!text) return '';
    return text.endsWith('/') ? text.slice(0, -1) : text;
  }

  function queryParam(name) {
    const u = new URL(window.location.href);
    return u.searchParams.get(name);
  }

  function getApiBase() {
    const q = queryParam('api_base');
    if (q) return normalizeBase(q);

    const cfg = window.SLUMPGUARD_CONFIG || {};
    if (cfg.apiBaseUrl) return normalizeBase(cfg.apiBaseUrl);

    const ls = localStorage.getItem(STORAGE_KEY);
    if (ls) return normalizeBase(ls);

    return 'http://127.0.0.1:8000';
  }

  function setApiBase(value) {
    const normalized = normalizeBase(value);
    if (!normalized) {
      localStorage.removeItem(STORAGE_KEY);
      return '';
    }
    localStorage.setItem(STORAGE_KEY, normalized);
    return normalized;
  }

  function apiUrl(pathOrUrl) {
    const p = String(pathOrUrl || '').trim();
    if (!p) return '';
    if (p === '#') return '#';
    if (p.startsWith('http://') || p.startsWith('https://')) return p;

    const base = getApiBase();
    if (!base) return p;

    try {
      return new URL(p, base).toString();
    } catch (_err) {
      return `${base}${p.startsWith('/') ? '' : '/'}${p}`;
    }
  }

  window.SG = {
    queryParam,
    getApiBase,
    setApiBase,
    apiUrl,
  };
})();
