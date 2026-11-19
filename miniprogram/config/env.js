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
    // PR7 domain cutover: the public hostname moves ams.istore-tech.cn ->
    // waypost.istore-tech.cn (Wenqu merge / rebrand). Both names are served by
    // the same nginx site and certificate SANs, so the old base keeps working
    // until retirement - but a release build must use the new one, and THIS
    // CLIENT MUST BE RE-RELEASED (ci.js upload + MP review) for the switch to
    // reach installed users. Before that release, the WeChat MP console
    // (开发管理 → 开发设置 → 服务器域名) must list waypost.istore-tech.cn in
    // all THREE whitelists - request, uploadFile and downloadFile. The rules
    // are HTTPS only, no port number, and an ICP-filed domain; this host
    // satisfies all three. See docs/DEPLOYMENT_MINIPROGRAM.md sections 3 and 4
    // and the domain-cutover runbook in docs/DEPLOYMENT.md.
    API_BASE: 'https://waypost.istore-tech.cn/api/v1',
  },
};

module.exports = {
  ENV,
  API_BASE: CONFIGS[ENV].API_BASE,
};
