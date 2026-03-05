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

    const isLocalHost = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
    if (isLocalHost) {
      return 'http://127.0.0.1:8000';
    }
    return '';
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

  function validateApiBase(baseValue) {
    const base = normalizeBase(baseValue || getApiBase());
    if (!base) {
      return 'API Base URL이 설정되지 않았습니다. 배포된 FastAPI URL(https://...)을 입력하세요.';
    }
    if (window.location.protocol === 'https:' && base.startsWith('http://')) {
      return 'HTTPS 페이지에서는 HTTP API를 호출할 수 없습니다. API Base를 https:// URL로 설정하세요.';
    }
    try {
      const u = new URL(base);
      if (u.hostname.endsWith('github.io')) {
        return 'GitHub Pages URL은 백엔드 API가 아닙니다. Render/Railway/서버의 FastAPI URL을 입력하세요.';
      }
    } catch (_err) {
      return 'API Base URL 형식이 올바르지 않습니다.';
    }
    return '';
  }

  function summarizeHtmlLike(text) {
    const snippet = String(text || '').replace(/\s+/g, ' ').trim().slice(0, 120);
    if (snippet.startsWith('<!DOCTYPE') || snippet.startsWith('<html')) {
      return 'HTML 페이지가 응답되었습니다. API Base URL이 잘못된 것 같습니다.';
    }
    return snippet || '(empty response)';
  }

  async function fetchJson(pathOrUrl, init) {
    const url = apiUrl(pathOrUrl);
    const res = await fetch(url, init);
    const contentType = (res.headers.get('content-type') || '').toLowerCase();

    if (contentType.includes('application/json')) {
      const data = await res.json();
      return { ok: res.ok, status: res.status, data, text: '', contentType, url };
    }

    const text = await res.text();
    const htmlHint = summarizeHtmlLike(text);
    return { ok: res.ok, status: res.status, data: null, text: htmlHint, contentType, url };
  }

  window.SG = {
    queryParam,
    getApiBase,
    setApiBase,
    apiUrl,
    validateApiBase,
    fetchJson,
    summarizeHtmlLike,
  };
})();
