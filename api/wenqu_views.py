"""Customer-facing REST endpoints for Wenqu (问渠), PR4.

Mounted under /api/v1/wenqu/ (see api.urls); the protected-upload download also
answers at the canonical /api/uploads/<path> because every URL the PR3 services
emit (file_store.build_protected_upload_url) points there.

Contract canon is helpdesk/backend/app/main.py - payload shapes, SSE frames,
meta keys, file_id semantics and the mixed-message history expansion are
preserved verbatim; only authentication (WenquJWTAuthentication with the tv
check + WenquProfile permissions) and the brand greeting copy differ.

Every view here pins ``authentication_classes = [WenquJWTAuthentication]`` (the
global default JWTAuthentication does NOT enforce the customer tv claim) and
``pagination_class = None`` (the global PageNumberPagination/20 would wrap the
bare arrays these contracts return in {count, results, ...}).

Test patch sites: turn/state helpers are called through the ``chat`` module
object (patch ``wenqu.services.chat.*``); provider/media functions are imported
by name into this module (patch ``api.wenqu_views.*``); SSE deltas are patched
at ``wenqu.services.sse.stream_reply_deltas``.
"""
from __future__ import annotations

import ipaddress
import logging
import mimetypes
import os
import tempfile
from urllib.parse import urlparse

from django.conf import settings
from django.http import FileResponse, HttpResponse
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from wenqu.models import Message
from wenqu.services import chat, registries, sse
from wenqu.services.chat import (
    ChatError,
    ChatInputBlocked,
    ChatSessionNotFound,
    RegenerateConflict,
    RegenerateUnavailable,
    upload_public_url,
)
from wenqu.services.llm.model_selector import (
    select_backup_asr_ref,
    select_backup_tts_ref,
    select_primary_asr_ref,
    select_primary_tts_ref,
    select_tts_default_voice,
)
from wenqu.services.llm.provider_router import (
    stream_speech_audio_with_fallback,
    synthesize_speech_with_fallback,
    transcribe_audio_with_fallback,
)
from wenqu.services.utils import moderation
from wenqu.services.utils.audio_utils import convert_to_mp3
from wenqu.services.utils.file_store import (
    delete_file_if_exists,
    open_upload,
    store_upload_bytes,
    upload_exists,
)

from .wenqu_authentication import (
    CanChat,
    WenquCustomerPermission,
    WenquJWTAuthentication,
    WenquQueryTokenAuthentication,
)
from .wenqu_serializers import (
    WenquMessageInSerializer,
    WenquRegenerateInSerializer,
    WenquSessionUpdateSerializer,
    WenquSpeakInSerializer,
)

logger = logging.getLogger(__name__)

# Wenqu chat attachments keep the helpdesk's own 15MB cap (main.py:1779) - NOT
# the staff mini-program's MAX_UPLOAD_SIZE (5MB): phone photos and voice notes
# are routinely larger, and nginx bounds the whole multipart body at 20m.
CHAT_UPLOAD_TYPES = {'image', 'audio'}
# Video is refused for the first release: WeChat only offers an asynchronous
# video check whose verdict arrives after the clip reached the agent (main.py:1780).
CHAT_UPLOAD_UNSUPPORTED_TYPES = {'video'}
CHAT_UPLOAD_MAX_BYTES = 15 * 1024 * 1024

# Extensions Volcengine file ASR accepts without an ffmpeg conversion (main.py:993).
_VOLCENGINE_ASR_EXTENSIONS = {'.mp3', '.wav', '.ogg', '.opus', '.pcm', '.raw'}


class WenquPayloadInvalid(APIException):
    """Serializer validation failure - 422, matching FastAPI's pydantic status."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = _('Invalid request payload.')
    default_code = 'invalid_payload'


def chat_error_response(exc: ChatError) -> Response:
    """Translate a chat domain error into the helpdesk's HTTPException shape."""
    if isinstance(exc, ChatInputBlocked):
        return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, ChatSessionNotFound):
        return Response({'detail': str(exc)}, status=status.HTTP_404_NOT_FOUND)
    if isinstance(exc, RegenerateConflict):
        return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
    if isinstance(exc, RegenerateUnavailable):
        return Response({'detail': str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    raise exc


class WenquAPIView(APIView):
    """Base wiring: tv-enforcing JWT auth, no pagination, customer-only access."""

    authentication_classes = [WenquJWTAuthentication]
    permission_classes = [WenquCustomerPermission]
    pagination_class = None

    def validated_payload(self, serializer_class):
        serializer = serializer_class(data=self.request.data)
        if not serializer.is_valid():
            raise WenquPayloadInvalid(serializer.errors)
        return serializer.validated_data

    def client_type(self, request) -> str:
        # The wenqu client is the WeChat mini program; the header is kept for
        # contract parity with the helpdesk's PC branch (main.py:2211).
        return request.headers.get('Helpdesk-Client-Type', 'wechat-mini')


# --- bootstrap / sessions -----------------------------------------------------


class WenquChatbotView(WenquAPIView):
    """GET /chatbot?session_id=&force_new= -> mini bootstrap (main.py:2208/:852).

    {greeting, session_id, restored, sessions[], external_user_id, messages[]}.
    """

    def get(self, request):
        force_new = str(request.query_params.get('force_new') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
        requested_session_id = str(request.query_params.get('session_id') or '').strip()
        try:
            bootstrap = chat.load_chat_bootstrap_state(
                request.user, session_id=requested_session_id or None, force_new=force_new,
            )
        except ChatError as exc:
            return chat_error_response(exc)
        return Response(
            chat.chatbot_client_payload(client_type=self.client_type(request), user=request.user, bootstrap=bootstrap)
        )


class WenquChatSessionListView(WenquAPIView):
    """GET /chat/sessions -> {sessions: [...]} (main.py:2266)."""

    permission_classes = [CanChat]

    def get(self, request):
        return Response({'sessions': chat.list_chat_session_summaries(request.user)})


class WenquChatSessionDetailView(WenquAPIView):
    """PATCH/DELETE /chat/sessions/<session_id> (main.py:2279/:2321).

    Deleting the *current* session additionally returns a fresh bootstrap so
    the client can switch conversations without a second round trip.
    """

    permission_classes = [CanChat]

    def patch(self, request, session_id):
        payload = self.validated_payload(WenquSessionUpdateSerializer)
        session = chat.resolve_user_chat_session(request.user, session_id)
        if session is None:
            return Response({'detail': str(_('未找到该会话或当前用户无权访问。'))}, status=status.HTTP_404_NOT_FOUND)

        normalized_title = ' '.join(str(payload.get('title') or '').split())
        if len(normalized_title) > chat.SESSION_TITLE_MAX_LENGTH:
            return Response(
                {'detail': str(_('会话名称不能超过 {max_length} 个字符。')).format(max_length=chat.SESSION_TITLE_MAX_LENGTH)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        session.title = normalized_title or None
        session.save(update_fields=['title', 'updated_at'])

        current_session_id = str(request.query_params.get('current_session_id') or '').strip()
        messages = chat.load_session_messages(request.user, session.session_id)
        return Response({
            'message': str(_('已清除自定义会话名称。')) if session.title is None else str(_('会话名称已更新。')),
            'session': chat.serialize_chat_session_summary(
                session, messages=messages, current_session_id=current_session_id,
            ),
            'sessions': chat.list_chat_session_summaries(request.user, current_session_id=current_session_id),
        })

    def delete(self, request, session_id):
        session = chat.resolve_user_chat_session(request.user, session_id)
        if session is None:
            return Response({'detail': str(_('未找到该会话或当前用户无权访问。'))}, status=status.HTTP_404_NOT_FOUND)

        normalized_session_id = str(session.session_id or '')
        current_session_id = str(request.query_params.get('current_session_id') or '').strip()
        current_session_deleted = current_session_id == normalized_session_id

        Message.objects.filter(user=request.user, session_id=normalized_session_id).delete()
        session.delete()

        response = {
            'message': str(_('当前会话已删除，已切换到其他会话。')) if current_session_deleted else str(_('会话已删除。')),
            'deleted_session_id': normalized_session_id,
            'current_session_deleted': current_session_deleted,
            'current_session_id': current_session_id,
        }
        if current_session_deleted:
            bootstrap = chat.load_chat_bootstrap_state(request.user)
            response['current_session_id'] = str(bootstrap.get('session_id') or '')
            response['sessions'] = bootstrap.get('session_summaries', [])
            response['bootstrap'] = chat.chatbot_client_payload(
                client_type=self.client_type(request), user=request.user, bootstrap=bootstrap,
            )
            return Response(response)

        response['sessions'] = chat.list_chat_session_summaries(request.user, current_session_id=current_session_id)
        return Response(response)


# --- synchronous chat ----------------------------------------------------------


class WenquChatView(WenquAPIView):
    """POST /chat {type, content, session_id?} -> {reply, session_id, meta} (main.py:2932)."""

    permission_classes = [CanChat]

    def post(self, request):
        payload = self.validated_payload(WenquMessageInSerializer)
        try:
            result = chat.handle_chat(request.user, payload)
        except ChatError as exc:
            return chat_error_response(exc)
        return Response(result)


class WenquChatRegenerateView(WenquAPIView):
    """POST /chat/regenerate {session_id, message_id?} (main.py:2979).

    A message_id that no longer matches the latest assistant reply answers 409.
    """

    permission_classes = [CanChat]

    def post(self, request):
        payload = self.validated_payload(WenquRegenerateInSerializer)
        try:
            result = chat.handle_regenerate(
                request.user, session_id=payload['session_id'], message_id=payload.get('message_id'),
            )
        except ChatError as exc:
            return chat_error_response(exc)
        return Response(result)


# --- streaming chat (SSE) -------------------------------------------------------


class WenquChatStreamView(WenquAPIView):
    """POST /chat/stream -> text/event-stream (main.py:3084).

    Lifecycle: can_chat -> ensure session -> input moderation (a violation is a
    plain JSON 400, never an SSE frame) -> persist user message -> identification
    or triage branch (start/final/done, no deltas) -> prepare_reply_generation ->
    start{session_id,meta} -> delta{text}* -> output moderation (replaces final
    only) -> persist assistant -> final{reply,session_id,meta} -> done{session_id}.
    The generator itself lives in wenqu.services.sse (sync WSGI, disconnect-safe).
    """

    permission_classes = [CanChat]

    def post(self, request):
        payload = self.validated_payload(WenquMessageInSerializer)
        try:
            turn = chat.prepare_stream_turn(request.user, payload)
        except ChatError as exc:
            return chat_error_response(exc)

        if turn.fixed_result is not None:
            return sse.sse_response(sse.fixed_reply_stream(
                session_id=turn.session_id, result=turn.fixed_result,
                start_meta=turn.fixed_start_meta or {},
            ))
        return sse.sse_response(sse.chat_event_stream(
            user=request.user, session_id=turn.session_id, preparation=turn.preparation,
            start_meta=chat.stream_start_meta(turn.preparation),
            regenerate=False, temperature=0.2,
        ))


class WenquChatRegenerateStreamView(WenquAPIView):
    """POST /chat/regenerate/stream -> SSE at temperature 0.45 (main.py:3022)."""

    permission_classes = [CanChat]

    def post(self, request):
        payload = self.validated_payload(WenquRegenerateInSerializer)
        try:
            preparation = chat.prepare_regenerate_stream(
                request.user, session_id=payload['session_id'], message_id=payload.get('message_id'),
            )
        except ChatError as exc:
            return chat_error_response(exc)
        return sse.sse_response(sse.chat_event_stream(
            user=request.user, session_id=payload['session_id'], preparation=preparation,
            start_meta=chat.stream_start_meta(preparation, regenerated=True),
            regenerate=True, temperature=0.45, final_meta_overrides={'regenerated': True},
        ))


# --- uploads / transcribe / speak ------------------------------------------------


class WenquFileUploadView(WenquAPIView):
    """POST /files/upload multipart name=file, formData type=image|audio (main.py:1786).

    -> {file_id, type, original_filename, url}. file_id is a uuid4 hex; the blob
    lives in private storage and url is the protected download path. Images pass
    WeChat imgSecCheck before anything is stored; video answers 422.
    """

    permission_classes = [CanChat]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        file_type = str(request.data.get('type') or 'image').strip().lower()
        if file_type in CHAT_UPLOAD_UNSUPPORTED_TYPES:
            return Response(
                {'detail': str(_('暂不支持发送视频，请改用图片或文字描述问题'))},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        if file_type not in CHAT_UPLOAD_TYPES:
            return Response({'detail': str(_('不支持的附件类型'))}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        uploaded = request.FILES.get('file')
        if uploaded is None:
            return Response({'detail': str(_('上传的附件为空'))}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        data = uploaded.read()
        if not data:
            return Response({'detail': str(_('上传的附件为空'))}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        if len(data) > CHAT_UPLOAD_MAX_BYTES:
            return Response(
                {'detail': str(_('附件超过 {limit} MB 上限')).format(limit=CHAT_UPLOAD_MAX_BYTES // (1024 * 1024))},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        if file_type == 'image':
            suggest = moderation.check_image(data, uploaded.content_type)
            if moderation.should_block(suggest):
                logger.warning('Blocked image upload from user %s (verdict=%s)', request.user.id, suggest.value)
                return Response(
                    {'detail': str(_('图片包含不适宜内容，无法发送'))},
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )

        original_filename = os.path.basename(uploaded.name or '') or f'{file_type}-attachment'
        stored = store_upload_bytes(
            user=request.user, filename=original_filename, data=data, file_type=file_type,
            mime_type=uploaded.content_type or 'application/octet-stream',
        )
        return Response({
            'file_id': stored.file_id,
            'type': file_type,
            'original_filename': stored.original_filename,
            'url': upload_public_url(stored),
        })


def _resolve_public_api_base_url() -> str:
    """Public base for Volcengine file ASR (main.py:931): it must be reachable
    from the provider, so loopback/private hosts are refused rather than silently
    producing an audio URL the ASR service can never fetch."""
    base_url = str(getattr(settings, 'PUBLIC_API_BASE_URL', '') or '').rstrip('/')
    if not base_url:
        raise RuntimeError('Volcengine ASR requires PUBLIC_API_BASE_URL to expose uploaded audio')
    parsed = urlparse(base_url)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise RuntimeError('PUBLIC_API_BASE_URL must be an absolute http(s) URL')
    hostname = str(parsed.hostname or '').strip().lower()
    if hostname in {'localhost', '0.0.0.0'}:
        raise RuntimeError('PUBLIC_API_BASE_URL must be publicly reachable for Volcengine ASR')
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return base_url
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        raise RuntimeError('PUBLIC_API_BASE_URL must be publicly reachable for Volcengine ASR')
    return base_url


def _prepare_public_asr_audio_source(file_path: str):
    """(candidate_path, cleanup_path, share_id, public_url) for Volcengine ASR."""
    prepared_path = file_path
    cleanup_path = ''
    if os.path.splitext(file_path)[1].strip().lower() not in _VOLCENGINE_ASR_EXTENSIONS:
        prepared_path = convert_to_mp3(file_path)
        cleanup_path = prepared_path
    share_id = registries.register_provider_audio_share(prepared_path)
    public_url = f'{_resolve_public_api_base_url()}/api/provider/audio/{share_id}'
    return prepared_path, cleanup_path, share_id, public_url


class WenquTranscribeView(WenquAPIView):
    """POST /transcribe multipart name=file, language? -> {text, model} (main.py:1839).

    The transcript becomes the user's message, so it passes msgSecCheck exactly
    like typed input (a violation is a plain JSON 400). Provider failure is 502,
    an empty transcript 422.
    """

    permission_classes = [CanChat]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        uploaded = request.FILES.get('file')
        if uploaded is None:
            return Response({'detail': str(_('上传的附件为空'))}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        language = str(request.data.get('language') or 'zh')

        suffix = os.path.splitext(uploaded.name or '')[1] or '.bin'
        fd, temp_path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, 'wb') as output:
            for chunk in uploaded.chunks():
                output.write(chunk)

        primary_model = select_primary_asr_ref().qualified_name
        fallback_model = select_backup_asr_ref().qualified_name
        # Volcengine file ASR fetches the audio over the public internet; prepare
        # a short-lived capability share when either candidate needs it.
        needs_public = primary_model.startswith('volcengine/') or fallback_model.startswith('volcengine/')
        candidate_path, cleanup_path, share_id, public_url = temp_path, '', '', None
        try:
            if needs_public:
                candidate_path, cleanup_path, share_id, public_url = _prepare_public_asr_audio_source(temp_path)
            try:
                result = transcribe_audio_with_fallback(
                    primary_model=primary_model, fallback_model=fallback_model,
                    file_path=candidate_path, language=language, public_file_url=public_url,
                )
            except Exception as exc:
                logger.exception('Audio transcription failed')
                return Response({'detail': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        finally:
            if share_id:
                registries.remove_provider_audio_share(share_id)
            if cleanup_path:
                delete_file_if_exists(cleanup_path)
            delete_file_if_exists(temp_path)

        transcript = str(result.get('text') or '').strip()
        if not transcript:
            return Response({'detail': 'Empty transcript'}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        suggest = moderation.check_text(transcript, chat.customer_openid(request.user))
        if moderation.should_block(suggest):
            logger.warning('Blocked transcribed audio from user %s (verdict=%s)', request.user.id, suggest.value)
            return Response(
                {'detail': str(_('您的消息包含不适宜内容，已停止处理，请修改后重试'))},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({'text': transcript, 'model': str(result.get('model_used') or primary_model)})


class WenquSpeakView(WenquAPIView):
    """POST /speak {text, message_id?, language_type?} (main.py:1907).

    -> {audio_url, model, voice, message_id}; audio_url is a protected download.
    """

    permission_classes = [CanChat]

    def post(self, request):
        payload = self.validated_payload(WenquSpeakInSerializer)
        reply_text = str(payload.get('text') or '').strip()
        if not reply_text:
            return Response({'detail': 'Empty text'}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        primary_model = select_primary_tts_ref().qualified_name
        fallback_model = select_backup_tts_ref().qualified_name
        primary_voice = select_tts_default_voice(primary_model)
        fallback_voice = select_tts_default_voice(fallback_model)
        try:
            result = synthesize_speech_with_fallback(
                primary_model=primary_model, fallback_model=fallback_model, text=reply_text,
                primary_voice=primary_voice, fallback_voice=fallback_voice,
                language_type=payload.get('language_type'),
            )
        except Exception as exc:
            logger.exception('Speech synthesis failed')
            return Response({'detail': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        return Response({
            'audio_url': result['audio_url'],
            'model': str(result.get('_model_used') or primary_model),
            'voice': str(result.get('_voice_used') or primary_voice),
            'message_id': str(payload.get('message_id') or ''),
        })


class WenquSpeakStreamView(WenquAPIView):
    """POST /speak/stream {text, ...} -> {stream_url, model, voice, message_id} (main.py:1947).

    PR4 adaptation: the mp3 bytes are synthesized eagerly and held in the cache
    registry (TTL 15min) instead of the helpdesk's lazy per-GET websocket stream,
    which a sync WSGI worker cannot keep open across requests. The client
    contract is unchanged: POST returns a stream_url, GET plays audio/mpeg.
    An IP throttle bounds synthesis blast-radius (cache-backed, Redis-safe).
    """

    permission_classes = [CanChat]

    def post(self, request):
        payload = self.validated_payload(WenquSpeakInSerializer)
        reply_text = str(payload.get('text') or '').strip()
        if not reply_text:
            return Response({'detail': 'Empty text'}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        window = int(getattr(settings, 'WENQU_SPEAK_STREAM_IP_WINDOW_SECONDS', registries.REGISTRY_TTL_SECONDS))
        max_hits = int(getattr(settings, 'WENQU_SPEAK_STREAM_IP_MAX_HITS', 30))
        if registries.ip_slot_exceeded(
            registries.client_ip(request), scope='speak_stream', window_seconds=window, max_hits=max_hits,
        ):
            return Response(
                {'detail': str(_('请求过于频繁，请稍后再试。'))},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        primary_model = select_primary_tts_ref().qualified_name
        fallback_model = select_backup_tts_ref().qualified_name
        primary_voice = select_tts_default_voice(primary_model)
        fallback_voice = select_tts_default_voice(fallback_model)
        try:
            audio = b''.join(
                chunk for chunk in stream_speech_audio_with_fallback(
                    primary_model=primary_model, fallback_model=fallback_model, text=reply_text,
                    primary_voice=primary_voice, fallback_voice=fallback_voice,
                    language_type=payload.get('language_type'),
                ) if chunk
            )
        except Exception as exc:
            logger.exception('Speech stream synthesis failed')
            return Response({'detail': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        if not audio:
            return Response({'detail': 'Empty audio stream'}, status=status.HTTP_502_BAD_GATEWAY)

        message_id = str(payload.get('message_id') or '')
        entry = registries.register_tts_stream(
            user=request.user, audio=audio, model=primary_model, voice=primary_voice, message_id=message_id,
        )
        return Response({
            'stream_url': f'/api/v1/wenqu/speak/stream/{entry["stream_id"]}',
            'model': primary_model,
            'voice': primary_voice,
            'message_id': message_id,
        })


class WenquSpeakStreamDownloadView(WenquAPIView):
    """GET /speak/stream/<stream_id> -> audio/mpeg from the cache registry (main.py:1982).

    Owner-scoped like the helpdesk (external_id match); an expired or foreign
    stream id answers 404. Accepts ?access_token= for WeChat audio playback.
    """

    authentication_classes = [WenquQueryTokenAuthentication]
    permission_classes = [WenquCustomerPermission]

    def get(self, request, stream_id):
        entry = registries.load_tts_stream(stream_id)
        if not entry or entry.get('external_id') != request.user.wenqu_profile.external_id:
            return Response({'detail': 'Audio stream not found'}, status=status.HTTP_404_NOT_FOUND)
        response = HttpResponse(entry.get('audio') or b'', content_type='audio/mpeg')
        response['Cache-Control'] = 'no-store'
        response['X-Accel-Buffering'] = 'no'
        return response


# --- protected downloads ----------------------------------------------------------


class WenquUploadDownloadView(WenquAPIView):
    """GET /api/uploads/<relative_path> (canonical) and /api/v1/wenqu/uploads/<...>.

    Protected download through storage.open (never .path): nginx serves the
    public MEDIA_ROOT world-readable, so customer blobs must only ever leave
    through this authenticated view (main.py:1113). Audio playback uses
    ?access_token= because WeChat's audio API cannot set headers.
    """

    authentication_classes = [WenquQueryTokenAuthentication]
    permission_classes = [WenquCustomerPermission]

    def get(self, request, relative_path):
        clean = str(relative_path or '').strip().replace('\\', '/')
        if not clean or clean.startswith('/') or '..' in clean.split('/'):
            return Response({'detail': 'File not found'}, status=status.HTTP_404_NOT_FOUND)
        if not upload_exists(clean):
            return Response({'detail': 'File not found'}, status=status.HTTP_404_NOT_FOUND)
        try:
            handle = open_upload(clean)
        except (FileNotFoundError, ValueError, OSError):
            return Response({'detail': 'File not found'}, status=status.HTTP_404_NOT_FOUND)
        media_type = mimetypes.guess_type(clean)[0] or 'application/octet-stream'
        return FileResponse(handle, content_type=media_type, filename=os.path.basename(clean))


class WenquProviderAudioView(APIView):
    """GET /api/provider/audio/<share_id> - short-lived capability URL for Volcengine ASR.

    Unauthenticated by design (the provider fetches it, main.py:1137): the uuid
    share id plus the 15-minute cache TTL are the guard, and the entry is removed
    as soon as the transcription finishes.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    pagination_class = None

    def get(self, request, share_id):
        share = registries.load_provider_audio_share(share_id)
        if not share:
            return Response({'detail': 'File not found'}, status=status.HTTP_404_NOT_FOUND)
        file_path = str(share.get('file_path') or '')
        if not file_path or not os.path.isfile(file_path):
            return Response({'detail': 'File not found'}, status=status.HTTP_404_NOT_FOUND)
        media_type = mimetypes.guess_type(file_path)[0] or 'application/octet-stream'
        response = FileResponse(
            open(file_path, 'rb'), content_type=media_type, filename=os.path.basename(file_path),
        )
        response['Cache-Control'] = 'no-store'
        return response