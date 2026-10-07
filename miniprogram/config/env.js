// Environment configuration.
//
// API_BASE must be an HTTPS host that is added to the WeChat MP console legal
// domains (request / uploadFile / downloadFile) for production. During local
// development you can enable "不校验合法域名" in WeChat DevTools and point at
// the Django dev server.
// Flip ENV to 'prod' when building a release candidate. It stays 'dev' in the
// repository so a fresh clone talks to the local Django dev server rather than
// to production by accident.
const ENV = 'dev'; // 'dev' | 'prod'

const CONFIGS = {
  dev: {
    API_BASE: 'http://127.0.0.1:8000/api/v1',
  },
  prod: {
    // Waypost production, served by the host Nginx on the shared ECS.
    //
    // Before a release build can actually reach this host, the WeChat MP console
    // (开发管理 → 开发设置 → 服务器域名) must list it in all THREE whitelists -
    // request, uploadFile and downloadFile. The rules are HTTPS only, no port
    // number, and an ICP-filed domain; this host satisfies all three.
    // See docs/DEPLOYMENT_MINIPROGRAM.md sections 3 and 4.
    API_BASE: 'https://ams.istore-tech.cn/api/v1',
  },
};

module.exports = {
  ENV,
  API_BASE: CONFIGS[ENV].API_BASE,
};
