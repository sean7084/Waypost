"""Staff admin pages for the Wenqu (问渠) helpdesk (PR5).

Django CBVs + server-rendered templates under ``/wenqu/`` (i18n_patterns), the
Waypost-native replacement for the standalone helpdesk's FastAPI ``/admin/*``
pages. Functional canon (helpdesk/backend/app/main.py):

* customers - company CRUD (``/admin/customers``);
* users - customer account / permission-bit CRUD + token-version bump
  (``/admin/users``); the bump invalidates every JWT minted before it (tv claim);
* file_upload - KB source upload -> ingest, list, preview, replace, delete
  (``/admin/upload`` + ``/admin/kb/*``);
* chatbot - the PC chat console with a session sidebar (``/chatbot``), added
  with the admin streaming views below.

Authentication is Waypost's own session/account system - no separate helpdesk
login, and the helpdesk's forgot_password page and orphan upload.html are
deliberately not ported. Every view gates on ``User.can_manage_wenqu()``
(superadmin / it_administrator) through ``WenquAdminMixin``, which mirrors
invoices.OrderManagementAccessMixin: denial is a message + dashboard redirect.

KB sources are operator-managed files under ``WENQU_KB_SOURCES_DIR`` (written
here with plain ``open()``, read by the PR3 ingest service); extracted images
and every customer-facing blob go through private storage inside the services.
No FieldFile path resolution or MEDIA_ROOT access anywhere (repo-hygiene gate).
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import uuid
from typing import Optional
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Count
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import (
    CreateView, DeleteView, FormView, ListView, RedirectView, TemplateView, UpdateView, View,
)

from wenqu.forms import CustomerForm, KBUploadForm, WenquProfileForm, WenquUserCreateForm
from wenqu.models import Customer, Message, WenquProfile
from wenqu.services import admin_chat, sse
from wenqu.services.chat import (
    ChatError, ChatInputBlocked, ChatSessionNotFound, RegenerateConflict, RegenerateUnavailable,
    resolve_user_chat_session, stream_start_meta,
)
from wenqu.services.kb import ingest
from wenqu.services.kb.paths import ensure_sources_root, resolve_stored_path
from wenqu.services.utils.file_store import delete_file_if_exists, kb_document_display_name

logger = logging.getLogger(__name__)


class WenquAdminMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Staff-only gate, mirroring invoices.OrderManagementAccessMixin."""

    def test_func(self):
        return self.request.user.can_manage_wenqu()

    def handle_no_permission(self):
        messages.error(self.request, _('You do not have access to Wenqu administration.'))
        return redirect('dashboard:dashboard')


class WenquAdminRootView(WenquAdminMixin, RedirectView):
    """``/wenqu/`` lands on the chat console, like the helpdesk's /admin/ root."""

    permanent = False

    def get_redirect_url(self, *args, **kwargs):
        return reverse('wenqu:chatbot')


# --- customers ---------------------------------------------------------------


class WenquCustomerListView(WenquAdminMixin, ListView):
    model = Customer
    template_name = 'wenqu/customer_list.html'
    context_object_name = 'customers'
    paginate_by = 20

    def get_queryset(self):
        queryset = Customer.objects.annotate(
            user_count=Count('users', distinct=True),
            document_count=Count('documents', distinct=True),
        )
        search = self.request.GET.get('search', '').strip()
        if search:
            queryset = queryset.filter(name__icontains=search)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search'] = self.request.GET.get('search', '')
        return context


class WenquCustomerCreateView(WenquAdminMixin, CreateView):
    model = Customer
    form_class = CustomerForm
    template_name = 'wenqu/customer_form.html'
    success_url = reverse_lazy('wenqu:customer_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Add Customer')
        context['action'] = _('Create Customer')
        return context

    def form_valid(self, form):
        messages.success(self.request, _('Customer created.'))
        return super().form_valid(form)


class WenquCustomerUpdateView(WenquAdminMixin, UpdateView):
    model = Customer
    form_class = CustomerForm
    template_name = 'wenqu/customer_form.html'
    success_url = reverse_lazy('wenqu:customer_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Edit Customer')
        context['action'] = _('Save Changes')
        return context

    def form_valid(self, form):
        messages.success(self.request, _('Customer updated.'))
        return super().form_valid(form)


class WenquCustomerDeleteView(WenquAdminMixin, DeleteView):
    model = Customer
    template_name = 'wenqu/customer_confirm_delete.html'
    success_url = reverse_lazy('wenqu:customer_list')
    context_object_name = 'customer'

    def form_valid(self, form):
        name = self.object.name
        response = super().form_valid(form)
        messages.success(self.request, _('Customer %(name)s deleted.') % {'name': name})
        return response


# --- customer users (WenquProfile) --------------------------------------------


class WenquUserListView(WenquAdminMixin, ListView):
    model = WenquProfile
    template_name = 'wenqu/user_list.html'
    context_object_name = 'profiles'
    paginate_by = 20

    def get_queryset(self):
        queryset = WenquProfile.objects.select_related('user', 'customer')
        search = self.request.GET.get('search', '').strip()
        if search:
            queryset = queryset.filter(external_id__icontains=search)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search'] = self.request.GET.get('search', '')
        return context


class WenquUserCreateView(WenquAdminMixin, FormView):
    form_class = WenquUserCreateForm
    template_name = 'wenqu/user_form.html'
    success_url = reverse_lazy('wenqu:user_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_create'] = True
        context['title'] = _('Add Customer User')
        context['action'] = _('Create User')
        return context

    def form_valid(self, form):
        profile = form.save()
        messages.success(
            self.request,
            _('Customer user %(name)s created.') % {'name': profile.external_id},
        )
        return redirect(self.success_url)


class WenquUserUpdateView(WenquAdminMixin, UpdateView):
    model = WenquProfile
    form_class = WenquProfileForm
    template_name = 'wenqu/user_form.html'
    success_url = reverse_lazy('wenqu:user_list')
    context_object_name = 'profile'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_create'] = False
        context['title'] = _('Edit Customer User')
        context['action'] = _('Save Changes')
        context['profile_user'] = self.object.user
        return context

    def form_valid(self, form):
        messages.success(self.request, _('Wenqu profile updated.'))
        return super().form_valid(form)


class WenquUserBumpTokenView(WenquAdminMixin, View):
    """POST-only: bump ``token_version`` so every JWT minted before it dies (tv)."""

    def post(self, request, pk):
        profile = get_object_or_404(WenquProfile, pk=pk)
        profile.token_version += 1
        profile.save(update_fields=['token_version', 'updated_at'])
        messages.success(
            request,
            _('Token version bumped for %(name)s; all existing sign-ins are now invalid.')
            % {'name': profile.external_id},
        )
        return redirect('wenqu:user_list')


class WenquUserDeleteView(WenquAdminMixin, DeleteView):
    """Delete the customer account (the User row); the profile cascades with it."""

    model = WenquProfile
    template_name = 'wenqu/user_confirm_delete.html'
    success_url = reverse_lazy('wenqu:user_list')
    context_object_name = 'profile'

    def form_valid(self, form):
        user = self.object.user
        name = self.object.external_id
        user.delete()
        messages.success(self.request, _('Customer user %(name)s deleted.') % {'name': name})
        return redirect(self.success_url)


# --- knowledge base -----------------------------------------------------------


def _selected_customer_id(request) -> Optional[int]:
    """Customer scope from the query string or form body; empty/invalid = global."""
    raw = str(request.POST.get('customer_id') or request.GET.get('customer_id') or '').strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _store_kb_source_upload(uploaded) -> str:
    """Write an uploaded source document under WENQU_KB_SOURCES_DIR.

    Mirrors the helpdesk's _store_kb_source_upload: a uuid4-hex prefix keeps
    same-name uploads distinct on disk while ``kb_document_display_name``
    strips it for the admin table. KB sources are operator files, not media -
    the ingest service reads them with plain open() (kb.paths).
    """
    root = ensure_sources_root()
    safe_name = os.path.basename(uploaded.name or 'document.txt')
    target = os.path.join(root, f'{uuid.uuid4().hex}_{safe_name}')
    with open(target, 'wb') as output_file:
        for chunk in uploaded.chunks():
            output_file.write(chunk)
    return target


class WenquKBView(WenquAdminMixin, TemplateView):
    """The KB operations hub: upload form + document table + preview panel."""

    template_name = 'wenqu/file_upload.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        customer_id = _selected_customer_id(self.request)
        context['form'] = KBUploadForm()
        context['customers'] = Customer.objects.all()
        context['selected_customer_id'] = customer_id
        context['initial_documents'] = ingest.list_customer_documents(customer_id=customer_id)
        return context


class WenquKBUploadView(WenquAdminMixin, View):
    """POST multipart: store each source file, then ingest (or same-name replace)."""

    def post(self, request):
        form = KBUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return JsonResponse({'detail': _('Upload rejected.'), 'errors': form.errors}, status=400)

        customer = form.cleaned_data['customer_id']
        customer_id = customer.id if customer else None
        conflict_mode = form.cleaned_data['conflict_mode']
        uploads = form.cleaned_data['files']

        results = []
        success_count = 0
        for upload in uploads:
            display_name = os.path.basename(upload.name or 'document.txt')
            stored_path = None
            try:
                stored_path = _store_kb_source_upload(upload)
                existing = None
                if conflict_mode == 'overwrite':
                    existing = ingest.find_customer_document_by_display_name(
                        customer_id=customer_id, display_name=display_name,
                    )
                if existing is not None:
                    ingest.replace_document(document=existing, path=stored_path, customer_id=customer_id)
                    message = _('Replaced %(name)s.') % {'name': display_name}
                    action = 'replaced'
                else:
                    ingest.ingest_one(stored_path, customer_id=customer_id)
                    message = _('Uploaded %(name)s.') % {'name': display_name}
                    action = 'uploaded'
                results.append({'filename': display_name, 'status': 'success', 'action': action, 'message': str(message)})
                success_count += 1
            except Exception as exc:
                # A failed ingest must not leave an orphan source file behind.
                if stored_path:
                    delete_file_if_exists(stored_path)
                logger.exception('KB upload failed for %s', display_name)
                results.append({'filename': display_name, 'status': 'error', 'action': 'failed', 'message': str(exc)})

        failure_count = len(results) - success_count
        if failure_count == 0:
            summary = _('All %(count)s documents uploaded.') % {'count': success_count}
        elif success_count == 0:
            summary = _('All %(count)s documents failed.') % {'count': failure_count}
        else:
            summary = _('%(success)s uploaded, %(failure)s failed.') % {'success': success_count, 'failure': failure_count}
        return JsonResponse({
            'message': str(summary),
            'success_count': success_count,
            'failure_count': failure_count,
            'results': results,
            'documents': ingest.list_customer_documents(customer_id=customer_id),
        })


class WenquKBDocumentsView(WenquAdminMixin, View):
    """GET JSON: customer-scoped document list (hydrates the table on scope change)."""

    def get(self, request):
        customer_id = _selected_customer_id(request)
        return JsonResponse({'documents': ingest.list_customer_documents(customer_id=customer_id)})


class WenquKBDocumentPreviewView(WenquAdminMixin, View):
    """GET JSON: extracted chunk text - what retrieval actually searches against."""

    def get(self, request, document_id):
        customer_id = _selected_customer_id(request)
        preview = ingest.get_document_preview(customer_id=customer_id, document_id=document_id)
        if preview is None:
            return JsonResponse({'detail': str(_('Document not found.'))}, status=404)
        return JsonResponse(preview)


class WenquKBDocumentDownloadView(WenquAdminMixin, View):
    """GET: stream the original source file inline (never a static alias)."""

    def get(self, request, document_id):
        customer_id = _selected_customer_id(request)
        document = ingest.get_customer_document(customer_id=customer_id, document_id=document_id)
        if document is None:
            raise Http404('Document not found')
        stored = getattr(document, 'path')
        resolved = resolve_stored_path(stored)
        if not os.path.isfile(resolved):
            logger.warning('KB document %s has no file at %s (stored path: %s)', document_id, resolved, stored)
            raise Http404('Document not found')
        display_name = kb_document_display_name(stored)
        media_type = mimetypes.guess_type(display_name)[0] or 'application/octet-stream'
        response = FileResponse(open(resolved, 'rb'), content_type=media_type)
        response['Content-Disposition'] = f"inline; filename*=UTF-8''{quote(display_name)}"
        return response


class WenquKBDocumentReplaceView(WenquAdminMixin, View):
    """POST multipart: row-level replacement, reusing the shared ingest logic."""

    def post(self, request, document_id):
        customer_id = _selected_customer_id(request)
        document = ingest.get_customer_document(customer_id=customer_id, document_id=document_id)
        if document is None:
            return JsonResponse({'detail': str(_('Document not found.'))}, status=404)
        uploaded = request.FILES.get('file')
        if uploaded is None:
            return JsonResponse({'detail': str(_('Please choose a replacement document.'))}, status=400)
        ext = os.path.splitext(uploaded.name or '')[1].lower()
        if ext not in ('.pdf', '.docx', '.doc', '.txt'):
            return JsonResponse({'detail': str(_('Unsupported file type. Use PDF, DOCX, DOC or TXT.'))}, status=400)

        stored_path = _store_kb_source_upload(uploaded)
        try:
            ingest.replace_document(document=document, path=stored_path, customer_id=customer_id)
        except Exception as exc:
            delete_file_if_exists(stored_path)
            logger.exception('KB replace failed for document %s', document_id)
            return JsonResponse({'detail': str(exc)}, status=400)
        display_name = kb_document_display_name(getattr(document, 'path'))
        return JsonResponse({
            'message': str(_('Replaced %(name)s.') % {'name': display_name}),
            'documents': ingest.list_customer_documents(customer_id=customer_id),
        })


class WenquKBDocumentDeleteView(WenquAdminMixin, View):
    """POST: remove retrieval rows AND the source artifact together (ingest service)."""

    def post(self, request, document_id):
        customer_id = _selected_customer_id(request)
        document = ingest.get_customer_document(customer_id=customer_id, document_id=document_id)
        if document is None:
            return JsonResponse({'detail': str(_('Document not found.'))}, status=404)
        display_name = kb_document_display_name(getattr(document, 'path'))
        ingest.delete_document(document=document)
        return JsonResponse({
            'message': str(_('Deleted %(name)s.') % {'name': display_name}),
            'documents': ingest.list_customer_documents(customer_id=customer_id),
        })


# --- chatbot (staff PC console) ------------------------------------------------
#
# Session-authenticated admin views, NOT the customer API: those endpoints pin
# WenquJWTAuthentication + WenquCustomerPermission (a WenquProfile and a live tv
# claim), which staff accounts do not have. The console therefore calls the
# shared services directly through wenqu.services.admin_chat (no identification
# branch, global KB scoping) and streams with the same wenqu.services.sse sync
# generators the customer SSE endpoints use. CSRF stays on: the page JS sends
# the X-CSRFToken header with every POST.


def _chat_error_response(exc: ChatError) -> JsonResponse:
    """Same status mapping as api.wenqu_views.chat_error_response, JSON shape."""
    if isinstance(exc, ChatInputBlocked):
        return JsonResponse({'detail': str(exc)}, status=400)
    if isinstance(exc, ChatSessionNotFound):
        return JsonResponse({'detail': str(exc)}, status=404)
    if isinstance(exc, RegenerateConflict):
        return JsonResponse({'detail': str(exc)}, status=409)
    if isinstance(exc, RegenerateUnavailable):
        return JsonResponse({'detail': str(exc)}, status=422)
    raise exc


def _json_payload(request) -> Optional[dict]:
    try:
        payload = json.loads(request.body or b'{}')
    except (ValueError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


class WenquChatbotView(WenquAdminMixin, TemplateView):
    """The PC chat console: session sidebar + history + composer."""

    template_name = 'wenqu/chatbot.html'

    def get(self, request, *args, **kwargs):
        force_new = str(request.GET.get('force_new') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
        requested = str(request.GET.get('session_id') or '').strip() or None
        try:
            state = admin_chat.load_admin_chat_state(request.user, session_id=requested, force_new=force_new)
        except ChatSessionNotFound:
            messages.error(request, _('Chat session not found.'))
            return redirect('wenqu:chatbot')
        context = self.get_context_data(**kwargs)
        context.update({
            'session_id': state['session_id'],
            'greeting': state['greeting'],
            'chat_history': state['page_history'],
            'latest_assistant_meta': state['latest_assistant_meta'],
            'session_summaries': state['session_summaries'],
            'staff_display': request.user.get_full_name_display(),
        })
        return self.render_to_response(context)


class WenquChatStreamView(WenquAdminMixin, View):
    """POST JSON {session_id?, type, content} -> text/event-stream.

    Lifecycle mirrors the customer stream endpoint: moderation failure is a
    plain JSON 400 *before* the stream starts; triage turns deliver
    start/final/done with no deltas; RAG turns stream start -> delta* -> final
    -> done through wenqu.services.sse.chat_event_stream.
    """

    def post(self, request):
        payload = _json_payload(request)
        if payload is None:
            return JsonResponse({'detail': str(_('Invalid JSON payload.'))}, status=400)
        payload.setdefault('type', 'text')
        try:
            turn = admin_chat.prepare_admin_stream_turn(request.user, payload)
        except ChatError as exc:
            return _chat_error_response(exc)

        if turn.fixed_result is not None:
            return sse.sse_response(sse.fixed_reply_stream(
                session_id=turn.session_id, result=turn.fixed_result,
                start_meta=turn.fixed_start_meta or {},
            ))
        return sse.sse_response(sse.chat_event_stream(
            user=request.user, session_id=turn.session_id, preparation=turn.preparation,
            start_meta=stream_start_meta(turn.preparation),
            regenerate=False, temperature=0.2,
        ))


class WenquChatRegenerateStreamView(WenquAdminMixin, View):
    """POST JSON {session_id, message_id?} -> SSE at temperature 0.45."""

    def post(self, request):
        payload = _json_payload(request)
        if payload is None or not str(payload.get('session_id') or '').strip():
            return JsonResponse({'detail': str(_('session_id is required.'))}, status=400)
        try:
            preparation = admin_chat.prepare_admin_regenerate_stream(
                request.user, session_id=payload['session_id'], message_id=payload.get('message_id'),
            )
        except ChatError as exc:
            return _chat_error_response(exc)
        return sse.sse_response(sse.chat_event_stream(
            user=request.user, session_id=payload['session_id'], preparation=preparation,
            start_meta=stream_start_meta(preparation, regenerated=True),
            regenerate=True, temperature=0.45, final_meta_overrides={'regenerated': True},
        ))


class WenquChatSessionRenameView(WenquAdminMixin, View):
    """POST JSON {title}: rename (or clear) a console session's custom title."""

    def post(self, request, session_id):
        session = resolve_user_chat_session(request.user, session_id)
        if session is None:
            return JsonResponse({'detail': str(_('Chat session not found.'))}, status=404)
        payload = _json_payload(request)
        if payload is None:
            return JsonResponse({'detail': str(_('Invalid JSON payload.'))}, status=400)
        title = ' '.join(str(payload.get('title') or '').split())
        if len(title) > 255:
            return JsonResponse({'detail': str(_('Title is too long (maximum 255 characters).'))}, status=400)
        session.title = title or None
        session.save(update_fields=['title', 'updated_at'])
        message = _('Session renamed.') if session.title else _('Session title cleared.')
        return JsonResponse({'message': str(message), 'session_id': session.session_id, 'title': session.title or ''})


class WenquChatSessionDeleteView(WenquAdminMixin, View):
    """POST JSON {current_session_id?}: delete a session and its messages."""

    def post(self, request, session_id):
        session = resolve_user_chat_session(request.user, session_id)
        if session is None:
            return JsonResponse({'detail': str(_('Chat session not found.'))}, status=404)
        payload = _json_payload(request) or {}
        current = str(payload.get('current_session_id') or request.GET.get('current_session_id') or '').strip()
        deleted_id = str(session.session_id)
        Message.objects.filter(user=request.user, session_id=deleted_id).delete()
        session.delete()
        current_deleted = bool(current) and current == deleted_id
        result = {
            'message': str(_('Session deleted.')),
            'deleted_session_id': deleted_id,
            'current_session_deleted': current_deleted,
        }
        if current_deleted:
            state = admin_chat.load_admin_chat_state(request.user)
            result['current_session_id'] = state['session_id']
        return JsonResponse(result)