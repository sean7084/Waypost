# Helpdesk Mini Frontend

This frontend uses an npm-managed uni-app Vue 3 CLI workflow for WeChat Mini Program development.

## Development

Install dependencies:

```powershell
npm install
```

Run the WeChat Mini Program dev build from VS Code or terminal:

```powershell
npm run dev:mp-weixin
```

Create a production WeChat Mini Program build:

```powershell
npm run build:mp-weixin
```

Build, refresh the currently open WeChat DevTools project, and generate a preview QR code:

```powershell
npm run preview:mp-weixin
```

Run frontend unit tests:

```powershell
npm test
```

## Source Layout

The CLI-managed Vue 3 source of truth lives under `src/`:

- `src/main.js`
- `src/App.vue`
- `src/pages.json`
- `src/manifest.json`
- `src/pages/`
- `src/components/`
- `src/utils/`

## WeChat DevTools

`npm run build:mp-weixin` builds to `dist/build/mp-weixin` and syncs DevTools project config into that directory. The local root `project.config.json` should point `miniprogramRoot` at this CLI output path.

With WeChat DevTools' service port enabled, the npm scripts call the HTTP API to reset file utilities, clean compile cache, and write a preview QR code to `preview-build-root.png`. The default `preview:mp-weixin` command reuses the currently open DevTools project instead of opening a new DevTools window each time. The default port is `25066`; override it when needed:

```powershell
$env:WECHAT_DEVTOOLS_PORT = "29139"
npm run preview:mp-weixin
```

Useful individual commands:

```powershell
npm run devtools:islogin
npm run devtools:open
npm run devtools:preview
```

Use `npm run devtools:open` only when DevTools is not already open or when it is pointing at the wrong compiled project.

Generated output directories such as `dist/` and `unpackage/` should not be committed.
