"""Static contract scan for the 泓问 native WeChat mini program client.

PR6 replaced the grafted uni-app/Vue client in ``hongwen-miniprogram/`` with a
native WXML/WXSS/JS/JSON mini program. The WeChat runtime cannot be exercised
from CI (no DevTools, no wx.login, no recorder), so the guarantee that the client
still speaks the PR4 API contract has to be static: read the shipped source and
assert the URLs, headers, refresh path, SSE framing and brand against the
backend's own route table.

Every expected URL is derived with ``django.urls.reverse`` rather than hardcoded,
so renaming a route in ``api/urls.py`` fails this scan instead of silently
breaking the client. Modelled on ``tests/test_repo_hygiene.py``: SimpleTestCase,
regex over the tree, no database, milliseconds to run.

What this does NOT cover (must be hand-tested in WeChat DevTools / on a device,
see hongwen-miniprogram/README.md): wx.login, profile/phone authorization, the
recorder meter, TTS playback, and the custom navigation-bar layout.
"""
import json
import re
from pathlib import Path

from django.test import SimpleTestCase
from django.urls import reverse

BASE_DIR = Path(__file__).resolve().parent.parent
CLIENT_DIR = BASE_DIR / "hongwen-miniprogram"

APP_JSON = CLIENT_DIR / "app.json"
PROJECT_CONFIG = CLIENT_DIR / "project.config.json"
PACKAGE_JSON = CLIENT_DIR / "package.json"
TOKENS_JSON = BASE_DIR / "static" / "design" / "tokens.json"

ENV_JS = CLIENT_DIR / "config" / "env.js"
API_JS = CLIENT_DIR / "utils" / "api.js"
REQUEST_JS = CLIENT_DIR / "utils" / "request.js"
SSE_JS = CLIENT_DIR / "utils" / "sse.js"
UTF8_JS = CLIENT_DIR / "utils" / "utf8.js"
IMAGE_JS = CLIENT_DIR / "utils" / "image.js"
CHAT_JS = CLIENT_DIR / "pages" / "chat" / "chat.js"
CHAT_WXML = CLIENT_DIR / "pages" / "chat" / "chat.wxml"
INDEX_WXML = CLIENT_DIR / "pages" / "index" / "index.wxml"
CI_JS = CLIENT_DIR / "scripts" / "ci.js"

HONGWEN_APPID = "wxd152caad9f9c76ef"
NAMESPACE_PREFIX = "/api/v1/hongwen"
BRAND = "泓问"

# Directories that are not part of the shipped client.
SKIPPED_DIR_NAMES = {"node_modules", ".git", "__pycache__", "coverage"}

# The four files every native page/component must ship.
NATIVE_SUFFIXES = (".js", ".json", ".wxml", ".wxss")

HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
GRADIENT_RE = re.compile(r"linear-gradient\s*\(")
CHOOSE_MEDIA_RE = re.compile(r"wx\.chooseMedia\s*\(")
UNI_API_RE = re.compile(r"\buni\.[a-zA-Z]")

# uni-app build chain that PR6 removed; none of it may come back.
REMOVED_UNI_APP_PATHS = [
    CLIENT_DIR / "src",
    CLIENT_DIR / "vite.config.js",
    CLIENT_DIR / "index.html",
    CLIENT_DIR / "minitest",
    CLIENT_DIR / "scripts" / "sync-env.js",
    CLIENT_DIR / "scripts" / "sync-devtools-project-config.js",
    CLIENT_DIR / "scripts" / "wechat-devtools.js",
]
UNI_APP_DEPENDENCY_NAMES = {"vue", "vite", "vite-plugin-uni"}
UNI_APP_DEPENDENCY_PREFIXES = ("@dcloudio/", "@vitejs/", "@vue/")


def _read(path):
    return path.read_text(encoding="utf-8")


def _json(path):
    return json.loads(_read(path))


def _walk(root):
    """Yield files under `root`, pruning vendored directories during traversal."""
    import os

    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIPPED_DIR_NAMES)
        for name in sorted(filenames):
            yield Path(dirpath) / name


def _relative(path):
    return path.relative_to(BASE_DIR).as_posix()


def _client_js_files():
    return [p for p in _walk(CLIENT_DIR) if p.suffix == ".js"]


def _shipped_js_files():
    """JS that actually ships to WeChat: vitest suites are packOptions-ignored,
    so a contract assertion inside a test must not read as a contract violation."""
    return [p for p in _client_js_files() if not p.name.endswith(".test.js")]


def _client_sources(suffixes):
    return [p for p in _walk(CLIENT_DIR) if p.suffix in suffixes]


def _namespace_path(route_name, **kwargs):
    """Backend route -> the path the client appends to API_BASE."""
    url = reverse(route_name, kwargs=kwargs or None)
    assert url.startswith(NAMESPACE_PREFIX), f"{route_name} moved out of the hongwen namespace: {url}"
    return url[len(NAMESPACE_PREFIX):]


class NativeStructureTests(SimpleTestCase):
    """The client must be a native mini program, not a compiled uni-app bundle."""

    def test_client_directory_exists(self):
        self.assertTrue(CLIENT_DIR.is_dir(), "hongwen-miniprogram/ disappeared")
        for name in ("app.js", "app.json", "app.wxss", "sitemap.json"):
            self.assertTrue((CLIENT_DIR / name).is_file(), f"missing {name}")

    def test_app_json_declares_the_two_pages(self):
        self.assertEqual(_json(APP_JSON)["pages"], ["pages/index/index", "pages/chat/chat"])

    def test_every_declared_page_and_component_ships_four_files(self):
        """app.json pages + every usingComponents entry must resolve to a complete
        WXML/WXSS/JS/JSON quartet, or DevTools fails to compile the project."""
        targets = list(_json(APP_JSON)["pages"])

        component_dirs = sorted((CLIENT_DIR / "components").glob("*"))
        targets.extend(f"components/{d.name}/{d.name}" for d in component_dirs if d.is_dir())

        # Follow usingComponents declarations too (page-level and app-level).
        # _walk prunes node_modules during traversal; rglob would enumerate it.
        json_files = [APP_JSON] + [p for p in _walk(CLIENT_DIR) if p.suffix == ".json"]
        for path in json_files:
            try:
                data = _json(path)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            for component_path in (data.get("usingComponents") or {}).values():
                targets.append(str(component_path).lstrip("/"))

        missing = []
        for target in sorted(set(targets)):
            for suffix in NATIVE_SUFFIXES:
                candidate = CLIENT_DIR / f"{target}{suffix}"
                if not candidate.is_file():
                    missing.append(_relative(candidate))
        self.assertEqual(missing, [], "Incomplete native page/component quartets: " + ", ".join(missing))

    def test_uni_app_build_chain_is_gone(self):
        present = [_relative(p) for p in REMOVED_UNI_APP_PATHS if p.exists()]
        self.assertEqual(present, [], "uni-app build chain must stay deleted: " + ", ".join(present))

    def test_package_json_has_no_uni_app_dependencies(self):
        package = _json(PACKAGE_JSON)
        deps = {**(package.get("dependencies") or {}), **(package.get("devDependencies") or {})}
        offenders = [
            name
            for name in deps
            if name in UNI_APP_DEPENDENCY_NAMES
            or name.startswith(UNI_APP_DEPENDENCY_PREFIXES)
            or "uni-app" in name
        ]
        self.assertEqual(offenders, [], "uni-app/Vue dependencies must stay removed: " + ", ".join(offenders))
        for tool in ("eslint", "miniprogram-ci", "vitest"):
            self.assertIn(tool, deps, f"{tool} is required by the native toolchain")

    def test_no_uni_runtime_api_calls_remain(self):
        offenders = [_relative(p) for p in _client_js_files() if UNI_API_RE.search(_read(p))]
        self.assertEqual(offenders, [], "uni.* APIs do not exist in a native mini program: " + ", ".join(offenders))

    def test_project_config_targets_the_native_root(self):
        config = _json(PROJECT_CONFIG)
        self.assertEqual(config["appid"], HONGWEN_APPID)
        self.assertEqual(config["compileType"], "miniprogram")
        self.assertIn(config["miniprogramRoot"], ("./", "."))
        self.assertIn(BRAND, config["description"])
        ignored = {entry["value"] for entry in config["packOptions"]["ignore"]}
        for entry in ("node_modules", "scripts", "package.json", "package-lock.json", "eslint.config.js"):
            self.assertIn(entry, ignored, f"packOptions must ignore {entry}")

    def test_private_project_config_is_git_ignored(self):
        gitignore = _read(CLIENT_DIR / ".gitignore")
        self.assertIn("project.private.config.json", gitignore)
        self.assertIn("node_modules/", gitignore)


class ApiContractTests(SimpleTestCase):
    """The client must call exactly the routes PR4 mounted, with the right shape."""

    def test_client_paths_match_backend_routes(self):
        cases = [
            ("hongwen-wx-login", {}, "'/auth/wx_login'"),
            ("hongwen-update-wx-user", {}, "'/auth/update_wx_user'"),
            ("hongwen-get-phone", {}, "'/auth/get-phone'"),
            ("hongwen-chatbot", {}, "`/chatbot"),
            ("hongwen-chat-sessions", {}, "'/chat/sessions'"),
            ("hongwen-chat", {}, "'/chat'"),
            ("hongwen-chat-stream", {}, "'/chat/stream'"),
            ("hongwen-chat-regenerate", {}, "'/chat/regenerate'"),
            ("hongwen-chat-regenerate-stream", {}, "'/chat/regenerate/stream'"),
            ("hongwen-file-upload", {}, "'/files/upload'"),
            ("hongwen-transcribe", {}, "'/transcribe'"),
            ("hongwen-speak", {}, "'/speak'"),
            ("hongwen-speak-stream", {}, "'/speak/stream'"),
        ]
        source = _read(API_JS)
        for route_name, kwargs, literal in cases:
            expected = _namespace_path(route_name, **kwargs)
            self.assertTrue(
                literal.strip("'`").startswith(expected),
                f"{route_name} resolves to {expected}, but the client literal is {literal}",
            )
            self.assertTrue(literal in source, f"utils/api.js never calls {expected} ({route_name})")

    def test_session_detail_path_is_parameterized(self):
        expected = _namespace_path("hongwen-chat-session-detail", session_id="SID")
        self.assertEqual(expected, "/chat/sessions/SID")
        source = _read(API_JS)
        self.assertIn("/chat/sessions/${encodeURIComponent(sessionId || '')}", source)
        self.assertIn("current_session_id=${encodeURIComponent(currentSessionId)}", source)
        self.assertIn("method: 'PATCH'", source)
        self.assertIn("method: 'DELETE'", source)

    def test_env_bases_are_the_hongwen_namespace(self):
        source = _read(ENV_JS)
        self.assertIn("http://127.0.0.1:8000/api/v1/hongwen", source)
        self.assertIn("https://waypost.istore-tech.cn/api/v1/hongwen", source)
        self.assertIn("const ENV = 'dev'", source, "a fresh clone must not talk to production")

    def test_refresh_uses_the_shared_simplejwt_route_outside_the_namespace(self):
        refresh_url = reverse("token-refresh")
        self.assertEqual(refresh_url, "/api/v1/auth/token/refresh/")
        self.assertFalse(refresh_url.startswith(NAMESPACE_PREFIX))
        env_source = _read(ENV_JS)
        self.assertIn("'/api/v1/auth/token/refresh/'", env_source)
        self.assertIn("API_ORIGIN", env_source)
        self.assertIn("env.REFRESH_URL", _read(REQUEST_JS))

    def test_bearer_and_client_type_headers_are_sent(self):
        source = _read(REQUEST_JS)
        self.assertIn("headers.Authorization = `Bearer ${token}`", source)
        self.assertIn("'Helpdesk-Client-Type'", source)
        self.assertIn("'wechat-mini'", source)

    def test_401_refreshes_once_then_retries(self):
        source = _read(REQUEST_JS)
        self.assertIn("res.statusCode === 401 && auth && retry", source)
        self.assertIn("await refreshAccessToken()", source)
        self.assertIn("retry: false", source, "the retry must not refresh again")
        self.assertIn("clearTokens()", source)
        self.assertIn("data: { refresh }", source)
        self.assertIn("res.data.access", source)

    def test_failed_refresh_clears_the_session_and_returns_to_login(self):
        source = _read(REQUEST_JS)
        self.assertIn("'/pages/index/index?loggedOut=1'", source)
        self.assertIn("wx.reLaunch", source)
        self.assertIn("UNAUTHORIZED", source)

    def test_uploads_are_multipart_name_file_with_a_type_field(self):
        api_source = _read(API_JS)
        request_source = _read(REQUEST_JS)
        self.assertIn(".uploadFile('/files/upload', filePath, 'file', { type })", api_source)
        self.assertIn("request.uploadFile('/transcribe', filePath, 'file', {})", api_source)
        self.assertIn("wx.uploadFile", request_source)
        self.assertIn("JSON.parse(res.data)", request_source, "uploadFile answers with a JSON string body")

    def test_video_upload_is_removed(self):
        api_source = _read(API_JS)
        self.assertIn("const UPLOAD_TYPES = ['image', 'audio'];", api_source)
        offenders = []
        for path in _shipped_js_files():
            text = _read(path)
            if "video_file_ids" in text or re.search(r"type:\s*'video'", text):
                offenders.append(_relative(path))
        self.assertEqual(offenders, [], "video upload was cut from the contract: " + ", ".join(offenders))
        self.assertIn("mediaType: ['image']", _read(IMAGE_JS))

    def test_streaming_uses_enable_chunked_and_on_chunk_received(self):
        source = _read(SSE_JS)
        self.assertIn("enableChunked: true", source)
        self.assertIn("responseType: 'arraybuffer'", source)
        self.assertIn("onChunkReceived", source)
        self.assertIn("method: 'POST'", source)

    def test_sse_lifecycle_events_are_handled(self):
        source = _read(API_JS)
        for event in ("start", "delta", "final", "done", "error"):
            self.assertIn(f"event === '{event}'", source, f"the {event} frame is not handled")
        self.assertIn("payload.text", source)
        self.assertIn("session_id", source)

    def test_meta_keys_are_consumed(self):
        chat_source = _read(CHAT_JS)
        self.assertIn("meta.attachments", chat_source)
        self.assertIn("meta.quick_replies", chat_source)
        self.assertIn("label_zh", chat_source)
        self.assertIn("item.type !== 'image'", chat_source)
        self.assertIn("quick_value", chat_source, "quick replies must send the triage value, not just the label")

    def test_utf8_is_decoded_by_hand(self):
        """The native runtime has no TextDecoder; a streamed Chinese reply needs
        the hand-written decoder, including across chunk boundaries."""
        self.assertTrue(UTF8_JS.is_file())
        source = _read(UTF8_JS)
        self.assertIn("createUtf8Decoder", source)
        self.assertIn("decodeUtf8", source)
        offenders = [_relative(p) for p in _shipped_js_files() if "new TextDecoder" in _read(p)]
        self.assertEqual(offenders, [], "TextDecoder is unavailable in the mini program runtime: " + ", ".join(offenders))
        self.assertIn("createUtf8Decoder", _read(SSE_JS))

    def test_media_urls_join_the_origin_not_the_namespace(self):
        """Uploads answer at root-canonical /api/uploads/<path>; prefixing them
        with API_BASE would double the namespace and 404 every image."""
        source = _read(REQUEST_JS)
        self.assertIn("function resolveAssetUrl", source)
        self.assertIn("env.API_ORIGIN", source)
        self.assertIn("access_token", source)
        self.assertIn("/[?&]access_token=/", source, "the token append must be idempotent")

    def test_mixed_history_expansion_is_rendered(self):
        source = _read(CHAT_JS)
        for key in ("filePath", "previewPath", "playable", "duration", "transcription"):
            self.assertIn(key, source, f"expanded audio/image content key {key} is not handled")


class BrandTests(SimpleTestCase):
    """Every customer-facing surface says 泓问, never helpdesk."""

    def test_navigation_and_pages_are_branded(self):
        self.assertEqual(_json(APP_JSON)["window"]["navigationBarTitleText"], BRAND)
        self.assertIn(BRAND, _read(INDEX_WXML))
        self.assertIn(BRAND, _read(CHAT_WXML))
        self.assertIn(BRAND, _json(PROJECT_CONFIG)["description"])

    def test_no_helpdesk_branding_in_ui_or_docs(self):
        offenders = []
        for path in _client_sources({".wxml", ".wxss", ".json", ".md"}):
            if "helpdesk" in _read(path).lower():
                offenders.append(_relative(path))
        self.assertEqual(offenders, [], "helpdesk branding leaked into the 泓问 client: " + ", ".join(offenders))

    def test_readme_documents_the_native_workflow(self):
        readme = _read(CLIENT_DIR / "README.md")
        self.assertIn(BRAND, readme)
        self.assertIn("npm run preview", readme)
        self.assertIn("手测", readme, "the manual DevTools checklist must stay documented")


class DesignTokenTests(SimpleTestCase):
    """The client shares the web palette; no hardcoded colours, no gradients."""

    def test_window_colours_match_tokens(self):
        tokens = _json(TOKENS_JSON)
        window = _json(APP_JSON)["window"]
        self.assertEqual(window["navigationBarBackgroundColor"].lower(), tokens["color"]["primary"].lower())
        self.assertEqual(window["backgroundColor"].lower(), tokens["color"]["bg"].lower())

    def test_wxss_uses_tokens_not_hex(self):
        offenders = []
        for path in _client_sources({".wxss"}):
            if path.name == "tokens.wxss":
                continue  # generated: it IS the token values
            match = HEX_COLOR_RE.search(_read(path))
            if match:
                offenders.append(f"{_relative(path)} ({match.group(0)})")
        self.assertEqual(offenders, [], "WXSS must use var(--wp-*): " + ", ".join(offenders))

    def test_no_decorative_gradients(self):
        offenders = [_relative(p) for p in _client_sources({".wxss", ".wxml"}) if GRADIENT_RE.search(_read(p))]
        self.assertEqual(offenders, [], "solid token colours only: " + ", ".join(offenders))

    def test_tokens_wxss_is_generated_and_imported(self):
        tokens_wxss = CLIENT_DIR / "styles" / "tokens.wxss"
        self.assertTrue(tokens_wxss.is_file(), "run scripts/build_design_tokens.py")
        self.assertIn("GENERATED FILE - DO NOT EDIT", _read(tokens_wxss))
        self.assertIn('@import "styles/tokens.wxss";', _read(CLIENT_DIR / "app.wxss"))


class CompressionTests(SimpleTestCase):
    """Photo capture must route through utils/image.js (same rule as miniprogram/)."""

    def test_pages_and_components_do_not_call_choose_media_directly(self):
        offenders = []
        for directory in (CLIENT_DIR / "pages", CLIENT_DIR / "components"):
            for path in _walk(directory):
                if path.suffix == ".js" and CHOOSE_MEDIA_RE.search(_read(path)):
                    offenders.append(_relative(path))
        self.assertEqual(offenders, [], "use utils/image.js pickPhotos(): " + ", ".join(offenders))

    def test_image_helper_compresses(self):
        source = _read(IMAGE_JS)
        self.assertIn("sizeType", source)
        self.assertIn("compressImage", source)
        self.assertIn("pickPhotos", source)

    def test_input_bar_uses_the_helper(self):
        source = _read(CLIENT_DIR / "components" / "input-bar" / "input-bar.js")
        self.assertIn("image.pickPhotos", source)
        self.assertIn("wx.getRecorderManager", source)


class CiScriptTests(SimpleTestCase):
    """preview/upload must be runnable locally with the hongwen upload key."""

    def test_ci_reads_the_hongwen_private_key(self):
        source = _read(CI_JS)
        self.assertIn("WECHAT_HONGWEN_CI_PRIVATE_KEY", source)
        self.assertIn("miniprogram-ci", source)
        self.assertIn("ci.preview", source)
        self.assertIn("ci.upload", source)
        self.assertIn(HONGWEN_APPID, source)

    def test_ci_ignores_node_tooling(self):
        source = _read(CI_JS)
        for entry in ("node_modules/**/*", "scripts/**/*", "package.json", "utils/*.test.js"):
            self.assertIn(entry, source)

    def test_npm_scripts_expose_preview_and_upload(self):
        scripts = _json(PACKAGE_JSON)["scripts"]
        self.assertEqual(scripts["preview"], "node scripts/ci.js preview")
        self.assertEqual(scripts["upload"], "node scripts/ci.js upload")
        self.assertEqual(scripts["test"], "vitest run")

    def test_util_suites_exist(self):
        for name in ("utf8", "sse", "request"):
            self.assertTrue((CLIENT_DIR / "utils" / f"{name}.test.js").is_file(), f"missing utils/{name}.test.js")