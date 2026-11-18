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

import logging

from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import APIView

from wenqu.models import Message
from wenqu.services import chat
from wenqu.services.chat import (
    ChatError,
    ChatInputBlocked,
    ChatSessionNotFound,
    RegenerateConflict,
    RegenerateUnavailable,
)

from .wenqu_authentication import (
    CanChat,
    WenquCustomerPermission,
    WenquJWTAuthentication,
)
from .wenqu_serializers import (
    WenquMessageInSerializer,
    WenquRegenerateInSerializer,
    WenquSessionUpdateSerializer,
)

logger = logging.getLogger(__name__)


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