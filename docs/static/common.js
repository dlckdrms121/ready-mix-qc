(function () {
  const STORAGE_KEY = 'slumpguard_api_base';
  const DEFAULT_LOCAL_BASE = 'http://127.0.0.1:8000';

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
    if (q) return sanitizeBase(q);

    const cfg = window.SLUMPGUARD_CONFIG || {};
    if (cfg.apiBaseUrl) return sanitizeBase(cfg.apiBaseUrl);

    const ls = localStorage.getItem(STORAGE_KEY);
    if (ls) return sanitizeBase(ls, true);

    const isLocalHost = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
    if (isLocalHost) {
      return DEFAULT_LOCAL_BASE;
    }
    return '';
  }

  function setApiBase(value) {
    const normalized = sanitizeBase(value);
    if (!normalized) {
      localStorage.removeItem(STORAGE_KEY);
      return '';
    }
    localStorage.setItem(STORAGE_KEY, normalized);
    return normalized;
  }

  function clearApiBase() {
    localStorage.removeItem(STORAGE_KEY);
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
    const base = sanitizeBase(baseValue || getApiBase());
    if (!base) {
      return 'API Base URL이 설정되지 않았습니다. 배포된 FastAPI URL(https://...)을 입력하세요.';
    }
    if (window.location.protocol === 'https:' && base.startsWith('http://')) {
      return 'HTTPS 페이지에서는 HTTP API를 호출할 수 없습니다. API Base를 https:// URL로 설정하세요.';
    }
    const blockedReason = getBlockedReason(base);
    if (blockedReason) return blockedReason;
    return '';
  }

  function getBlockedReason(base) {
    try {
      const u = new URL(base);
      if (u.hostname.endsWith('github.io')) {
        return 'GitHub Pages URL은 백엔드 API가 아닙니다. Render/Railway/서버의 FastAPI URL을 입력하세요.';
      }
      if (u.pathname.includes('/ready-mix-qc')) {
        return '현재 프론트 주소가 입력되었습니다. 백엔드 FastAPI 서버 주소(예: https://xxx.onrender.com)만 입력하세요.';
      }
    } catch (_err) {
      return 'API Base URL 형식이 올바르지 않습니다.';
    }
    return '';
  }

  function sanitizeBase(value, cleanupStorage) {
    const normalized = normalizeBase(value);
    if (!normalized) return '';

    const blockedReason = getBlockedReason(normalized);
    if (blockedReason) {
      if (cleanupStorage) {
        localStorage.removeItem(STORAGE_KEY);
      }
      return '';
    }
    return normalized;
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
    clearApiBase,
    apiUrl,
    validateApiBase,
    fetchJson,
    summarizeHtmlLike,
  };
})();
