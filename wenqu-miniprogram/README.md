# 问渠小程序（原生）

问渠是 Waypost 的客户自助排障助手。本目录是它的**微信原生小程序**客户端：
WXML / WXSS / JS / JSON 四件套，无 uni-app、无 Vue、无构建步骤——微信开发者工具
直接打开本目录即可运行。

- AppID：`wxd152caad9f9c76ef`（独立于巡检小程序）
- 后端：Waypost Django，客户 API 命名空间 `/api/v1/wenqu`
- 品牌：所有界面文案均为「问渠」

## 目录结构

```
app.js / app.json / app.wxss     全局入口、页面注册、token 化全局样式
config/env.js                    手翻 ENV（dev / prod）与 API_BASE / API_ORIGIN
styles/tokens.wxss               由 static/design/tokens.json 生成，勿手改
pages/index/                     wx.login → /auth/wx_login → 进入聊天
pages/chat/                      bootstrap、会话抽屉、流式渲染、语音/TTS
components/input-bar/            文本、图片（压缩）、录音
components/message-list/         mixed 展开、语音播放、附件预览、消息操作
utils/request.js                 Bearer + 401 刷新一次重试 + uploadFile/downloadFile
utils/sse.js                     enableChunked + onChunkReceived 的 SSE 传输与分帧
utils/utf8.js                    手写 UTF-8 解码（原生微信无 TextDecoder）
utils/api.js                     端点封装与 start/delta/final/done/error 生命周期
utils/audio.js                   TTS 下载、本地持久化、可播放 URL
utils/image.js                   chooseMedia → compressImage → saveFile
utils/auth.js                    会话/资料存储
utils/header-metrics.js          自定义导航栏高度与状态栏内缩
scripts/ci.js                    miniprogram-ci preview / upload
static/icons/                    消息操作图标
```

## 开发

```bash
npm ci
npm run lint
npm test
```

微信开发者工具 → 导入项目 → 选择本目录（`miniprogramRoot` 为 `./`）。
本地联调时勾选「不校验合法域名」，`config/env.js` 的 `ENV` 保持 `'dev'`，
指向 `http://127.0.0.1:8000/api/v1/wenqu`。

发布前**手工**把 `config/env.js` 的 `ENV` 翻成 `'prod'`
（`https://waypost.istore-tech.cn/api/v1/wenqu`），并确认该域名已加入
微信公众平台「开发管理 → 开发设置 → 服务器域名」的 request / uploadFile /
downloadFile **三个**白名单。仓库里始终保持 `'dev'`，避免新克隆误连生产。

## 设计 token

`styles/tokens.wxss` 由仓库根的设计 token 生成器统一产出，与 Web 端和巡检小程序
共用同一份 `static/design/tokens.json`：

```bash
python ../scripts/build_design_tokens.py          # 重新生成
python ../scripts/build_design_tokens.py --check  # 校验漂移
```

所有 WXSS 只允许使用 `var(--wp-*)`，不得写死色值。

## 真机预览 / 上传

```bash
npm run preview    # 生成 preview-qr.jpg，用微信扫码真机体验
npm run upload     # 推送开发版本到公众平台（版本管理 → 开发版本）
node scripts/ci.js preview --dry-run   # 只校验 appid + 私钥配置
```

`scripts/ci.js` 从仓库根 `.env` 读取：

| 变量 | 说明 |
| --- | --- |
| `WECHAT_WENQU_CI_PRIVATE_KEY` | `private.wxd152caad9f9c76ef.key` 的路径，或私钥内容本身 |
| `WECHAT_WENQU_APPID` | 可选，默认取 `project.config.json` 的 appid |

私钥在公众平台「开发管理 → 开发设置 → 小程序代码上传」下载，**不要提交**。
若报 errCode `-10008`（invalid ip），关闭该页面的 IP 白名单或把本机公网 IP 加进去。

## 必须在开发者工具 / 真机手测的项目

终端里的 lint 与 vitest 只覆盖纯逻辑（UTF-8、SSE 分帧、401 刷新）。以下依赖
微信运行时，**无法在 CI 中自动验证**，每次改动后请手测：

1. `wx.login` → `wx_login`：老用户直接进聊天；新用户出现「授权登录」按钮。
2. 头像昵称授权（`wx.getUserProfile` → `update_wx_user`）。注意微信自 2022-10-25
   起对该接口返回匿名资料，属预期行为。
3. 手机号授权横幅（`open-type="getPhoneNumber"` → `auth/get-phone`）。
4. 会话抽屉：新建、切换、重命名（可编辑弹窗）、删除当前会话后自动 bootstrap。
5. 流式回复：打字机逐字出现、`quick_replies` 排障按钮可点、附件图片可预览。
6. 语音录制：电平条随音量起伏、取消/发送两条路径、转写文本回填、自动朗读回复。
7. TTS 播放：`speak/stream` 直连与 `speak` 下载回退两条路径、再次点击停止。
8. 重答（regenerate）：仅最新回复可用，失败时回滚原文。
9. 图片上传：相册/拍照两条入口，压缩后 ≤15MB，附加后随文字/语音一起提交。
10. access 过期（2h）后自动用 refresh 换新并重试；refresh 失效时清态回到登录页。
11. 自定义导航栏在刘海屏/胶囊按钮下不遮挡标题，抽屉顶部对齐。

## 相关文档

- [设计系统](../docs/DESIGN_SYSTEM.md)
- [巡检小程序（原生约定参照）](../miniprogram/README.md)