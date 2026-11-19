// Environment configuration for the 问渠 mini program.
//
// API_BASE is the Wenqu customer namespace on the Waypost backend. It must be an
// HTTPS host listed in the WeChat MP console legal domains (request / uploadFile
// / downloadFile) for production. During local development enable
// "不校验合法域名" in WeChat DevTools and point at the Django dev server.
//
// Flip ENV to 'prod' by hand when building a release candidate. It stays 'dev'
// in the repository so a fresh clone talks to the local Django dev server rather
// than to production by accident (same convention as miniprogram/config/env.js).
const ENV = 'dev'; // 'dev' | 'prod'

const CONFIGS = {
  dev: {
    API_BASE: 'http://127.0.0.1:8000/api/v1/wenqu',
  },
  prod: {
    // Waypost production, served by the host Nginx on the shared ECS.
    //
    // Before a release build can reach this host, the WeChat MP console
    // (开发管理 → 开发设置 → 服务器域名) must list it in all THREE whitelists -
    // request, uploadFile and downloadFile. HTTPS only, no port, ICP-filed.
    API_BASE: 'https://waypost.istore-tech.cn/api/v1/wenqu',
  },
};

const API_BASE = CONFIGS[ENV].API_BASE;

// Scheme + host + port of API_BASE, without the /api/v1/wenqu path.
//
// Media URLs are root-canonical on the server: uploads answer at
// `/api/uploads/<path>` and provider audio at `/api/provider/audio/<id>`
// (waypost/urls.py), deliberately OUTSIDE the wenqu namespace. They must be
// joined to the origin, never to API_BASE, or the path would be doubled.
const API_ORIGIN = API_BASE.replace(/^(https?:\/\/[^/]+)[\s\S]*$/i, '$1');

// SimpleJWT refresh is the shared Waypost route, also outside the namespace.
// ROTATE_REFRESH_TOKENS is False, so the response carries `access` only and the
// stored 14-day refresh token keeps working until it expires.
const REFRESH_PATH = '/api/v1/auth/token/refresh/';

module.exports = {
  ENV,
  API_BASE,
  API_ORIGIN,
  REFRESH_URL: `${API_ORIGIN}${REFRESH_PATH}`,
};