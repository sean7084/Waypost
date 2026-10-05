from unittest.mock import patch

from django.core.mail import send_mail
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import AdminRole, SystemSMTPSettings, User


class UserEditViewTests(TestCase):
	def extract_temporary_password(self, email_body):
		for line in email_body.splitlines():
			if line.startswith('Temporary password: '):
				return line.split(': ', 1)[1]
		self.fail('Temporary password not found in email body.')

	def assertLabelMarkedRequired(self, response, field_id, label_text):
		self.assertInHTML(
			f'<label for="{field_id}" class="form-label">{label_text} <span class="text-danger">*</span></label>',
			response.content.decode(response.charset),
		)

	def assertLabelNotMarkedRequired(self, response, field_id, label_text):
		self.assertInHTML(
			f'<label for="{field_id}" class="form-label">{label_text}</label>',
			response.content.decode(response.charset),
		)

	@classmethod
	def setUpTestData(cls):
		cls.admin_user = User.objects.create_superuser(
			username='admin_user',
			email='admin@example.com',
			password='AdminPass123!'
		)
		cls.viewer_role = AdminRole.objects.get(code=User.AdminRole.VIEWER)
		cls.order_management_manager_role = AdminRole.objects.get(code=User.AdminRole.ORDER_MANAGEMENT_MANAGER)

		cls.target_user = User.objects.create_user(
			username='target_user',
			email='target@example.com',
			password='TargetPass123!',
			first_name='Target',
			last_name='User',
			language_preference='en-us',
			timezone='UTC',
		)
		cls.target_user.roles.add(cls.viewer_role)

		cls.order_management_manager_user = User.objects.create_user(
			username='order_manager_user',
			email='order-manager@example.com',
			password='OrderPass123!',
			first_name='Order',
			last_name='Manager',
			language_preference='en-us',
			timezone='UTC',
		)
		cls.order_management_manager_user.roles.add(cls.order_management_manager_role)

	def test_create_page_renders_required_fields_with_markers(self):
		self.client.force_login(self.admin_user)

		response = self.client.get(reverse('accounts:user_create'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'name="language_preference"', html=False)
		self.assertContains(response, 'name="timezone"', html=False)
		self.assertContains(response, 'name="must_change_password"', html=False)
		self.assertNotContains(response, 'name="password1"', html=False)
		self.assertNotContains(response, 'name="password2"', html=False)
		self.assertNotContains(response, 'name="use_random_password"', html=False)
		self.assertLabelMarkedRequired(response, 'id_username', 'Username')
		self.assertLabelMarkedRequired(response, 'id_first_name', 'First Name')
		self.assertLabelMarkedRequired(response, 'id_last_name', 'Last Name')
		self.assertLabelMarkedRequired(response, 'id_email', 'Email Address')
		self.assertLabelMarkedRequired(response, 'id_language_preference', 'Language Preference')
		self.assertLabelMarkedRequired(response, 'id_timezone', 'Timezone')
		self.assertLabelNotMarkedRequired(response, 'id_roles', 'Administrator Roles')

	def test_edit_page_renders_required_settings_fields(self):
		self.client.force_login(self.admin_user)

		response = self.client.get(reverse('accounts:user_edit', args=[self.target_user.pk]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'name="language_preference"', html=False)
		self.assertContains(response, 'name="timezone"', html=False)
		self.assertContains(response, 'name="must_change_password"', html=False)
		self.assertNotContains(response, 'name="password1"', html=False)
		self.assertNotContains(response, 'name="password2"', html=False)
		self.assertNotContains(response, 'name="division"', html=False)
		self.assertNotContains(response, 'name="managed_divisions"', html=False)
		self.assertLabelMarkedRequired(response, 'id_username', 'Username')
		self.assertLabelMarkedRequired(response, 'id_first_name', 'First Name')
		self.assertLabelMarkedRequired(response, 'id_last_name', 'Last Name')
		self.assertLabelMarkedRequired(response, 'id_email', 'Email Address')
		self.assertLabelMarkedRequired(response, 'id_language_preference', 'Language Preference')
		self.assertLabelMarkedRequired(response, 'id_timezone', 'Timezone')
		self.assertLabelNotMarkedRequired(response, 'id_roles', 'Administrator Roles')

	@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
	def test_create_user_allows_blank_employee_id_with_legacy_blank_record(self):
		self.client.force_login(self.admin_user)

		legacy_user = User.objects.create_user(
			username='legacy_blank_employee',
			email='legacy-blank@example.com',
			password='LegacyPass123!',
			first_name='Legacy',
			last_name='Blank',
			language_preference='en-us',
			timezone='UTC',
		)
		User.objects.filter(pk=legacy_user.pk).update(employee_id='')

		response = self.client.post(
			reverse('accounts:user_create'),
			{
				'username': 'blank_employee_create',
				'email': 'blank-employee-create@example.com',
				'first_name': 'Blank',
				'last_name': 'Create',
				'employee_id': '',
				'phone_number': '',
				'department': '',
				'job_title': '',
				'company': '',
				'manager': '',
				'roles': [],
				'managed_company': '',
				'managed_divisions': [],
				'managed_locations': [],
				'language_preference': 'en-us',
				'timezone': 'UTC',
				'is_active': 'on',
				'must_change_password': 'on',
			},
		)

		self.assertRedirects(response, reverse('accounts:user_list'))

		created_user = User.objects.get(username='blank_employee_create')
		self.assertIsNone(created_user.employee_id)
		self.assertEqual(len(mail.outbox), 1)
		temporary_password = self.extract_temporary_password(mail.outbox[0].body)
		self.assertTrue(created_user.check_password(temporary_password))

	def test_editing_self_keeps_session_and_password(self):
		self.client.force_login(self.admin_user)

		response = self.client.post(
			reverse('accounts:user_edit', args=[self.admin_user.pk]),
			{
				'username': self.admin_user.username,
				'email': 'admin-updated@example.com',
				'first_name': 'Admin',
				'last_name': 'Updated',
				'employee_id': '',
				'phone_number': '',
				'department': '',
				'job_title': '',
				'company': '',
				'division': '',
				'manager': '',
				'roles': [],
				'managed_company': '',
				'managed_divisions': [],
				'managed_locations': [],
				'language_preference': 'en-us',
				'timezone': 'UTC',
				'is_active': 'on',
				'is_staff': 'on',
				'must_change_password': 'on',
			},
		)

		self.assertRedirects(response, reverse('accounts:user_list'))

		self.admin_user.refresh_from_db()
		self.assertEqual(self.admin_user.email, 'admin-updated@example.com')
		self.assertTrue(self.admin_user.check_password('AdminPass123!'))
		self.assertEqual(self.client.session.get('_auth_user_id'), str(self.admin_user.pk))

		list_response = self.client.get(reverse('accounts:user_list'))
		self.assertEqual(list_response.status_code, 200)

	def test_editing_another_user_does_not_switch_authenticated_session(self):
		self.client.force_login(self.admin_user)

		response = self.client.post(
			reverse('accounts:user_edit', args=[self.target_user.pk]),
			{
				'username': self.target_user.username,
				'email': 'target-updated@example.com',
				'first_name': 'Target',
				'last_name': 'Updated',
				'employee_id': '',
				'phone_number': '',
				'department': '',
				'job_title': '',
				'company': '',
				'division': '',
				'manager': '',
				'roles': [str(self.viewer_role.pk)],
				'managed_company': '',
				'managed_divisions': [],
				'managed_locations': [],
				'language_preference': 'en-us',
				'timezone': 'UTC',
				'is_active': 'on',
				'must_change_password': 'on',
			},
		)

		self.assertRedirects(response, reverse('accounts:user_list'))

		self.target_user.refresh_from_db()
		self.assertEqual(self.target_user.email, 'target-updated@example.com')
		self.assertTrue(self.target_user.check_password('TargetPass123!'))
		self.assertEqual(self.client.session.get('_auth_user_id'), str(self.admin_user.pk))

	def test_user_list_renders_order_management_access_scope(self):
		self.client.force_login(self.admin_user)

		response = self.client.get(reverse('accounts:user_list'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Order management management and price approvals')
		self.assertContains(response, reverse('accounts:user_reset_password', args=[self.target_user.pk]))
		self.assertNotContains(response, reverse('accounts:user_reset_password', args=[self.admin_user.pk]))

	@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
	def test_reset_password_sends_email_and_updates_password(self):
		self.client.force_login(self.admin_user)

		response = self.client.post(reverse('accounts:user_reset_password', args=[self.target_user.pk]))

		self.assertRedirects(response, reverse('accounts:user_list'))
		self.assertEqual(len(mail.outbox), 1)

		self.target_user.refresh_from_db()
		temporary_password = self.extract_temporary_password(mail.outbox[0].body)
		self.assertTrue(self.target_user.must_change_password)
		self.assertTrue(self.target_user.check_password(temporary_password))
		self.assertFalse(self.target_user.check_password('TargetPass123!'))
		self.assertEqual(self.client.session.get('_auth_user_id'), str(self.admin_user.pk))

	def test_settings_page_shows_system_smtp_form_for_superadmin(self):
		self.client.force_login(self.admin_user)

		response = self.client.get(reverse('accounts:settings'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'name="system_smtp-smtp_host"', html=False)
		self.assertContains(response, 'name="system_smtp-from_email"', html=False)

	def test_settings_page_hides_system_smtp_form_for_standard_user(self):
		self.client.force_login(self.target_user)

		response = self.client.get(reverse('accounts:settings'))

		self.assertEqual(response.status_code, 200)
		self.assertNotContains(response, 'name="system_smtp-smtp_host"', html=False)

	def test_superadmin_can_save_system_smtp_settings(self):
		self.client.force_login(self.admin_user)

		response = self.client.post(
			reverse('accounts:settings'),
			{
				'system_smtp-from_email': 'noreply@example.com',
				'system_smtp-from_display_name': 'Waypost',
				'system_smtp-username': 'smtp-user',
				'system_smtp-password': 'smtp-pass-123',
				'system_smtp-smtp_host': 'smtp.example.com',
				'system_smtp-smtp_port': '587',
				'system_smtp-smtp_security': SystemSMTPSettings.ConnectionSecurity.STARTTLS,
				'system_smtp-timeout': '20',
				'system_smtp-is_active': 'on',
				'save_system_smtp': '1',
			},
		)

		self.assertRedirects(response, reverse('accounts:settings'))

		smtp_settings = SystemSMTPSettings.get_solo()
		self.assertEqual(smtp_settings.from_email, 'noreply@example.com')
		self.assertEqual(smtp_settings.from_display_name, 'Waypost')
		self.assertEqual(smtp_settings.username, 'smtp-user')
		self.assertEqual(smtp_settings.smtp_host, 'smtp.example.com')
		self.assertEqual(smtp_settings.smtp_port, 587)
		self.assertEqual(smtp_settings.timeout, 20)
		self.assertTrue(smtp_settings.is_active)
		self.assertEqual(smtp_settings.password, 'smtp-pass-123')

	@override_settings(EMAIL_BACKEND='accounts.email_backends.DatabaseSMTPEmailBackend', DEFAULT_FROM_EMAIL='', TEST_OUTBOUND_EMAIL_OVERRIDE='')
	def test_database_email_backend_uses_saved_system_smtp_settings(self):
		smtp_settings = SystemSMTPSettings.get_solo()
		smtp_settings.from_email = 'noreply@example.com'
		smtp_settings.from_display_name = 'Waypost'
		smtp_settings.username = 'smtp-user'
		smtp_settings.smtp_host = 'smtp.example.com'
		smtp_settings.smtp_port = 587
		smtp_settings.smtp_security = SystemSMTPSettings.ConnectionSecurity.STARTTLS
		smtp_settings.timeout = 25
		smtp_settings.is_active = True
		smtp_settings.set_password('smtp-pass-123')
		smtp_settings.save()

		captured = {}

		class DummySMTPBackend:
			def __init__(self, *args, **kwargs):
				captured['kwargs'] = kwargs

			def open(self):
				return True

			def close(self):
				return None

			def send_messages(self, email_messages):
				captured['messages'] = email_messages
				return len(email_messages)

		with patch('accounts.email_backends.SMTPEmailBackend', DummySMTPBackend):
			sent_count = send_mail('SMTP Test', 'Hello', None, ['recipient@example.com'], fail_silently=False)

		self.assertEqual(sent_count, 1)
		self.assertEqual(captured['kwargs']['host'], 'smtp.example.com')
		self.assertEqual(captured['kwargs']['port'], 587)
		self.assertEqual(captured['kwargs']['username'], 'smtp-user')
		self.assertEqual(captured['kwargs']['password'], 'smtp-pass-123')
		self.assertTrue(captured['kwargs']['use_tls'])
		self.assertFalse(captured['kwargs']['use_ssl'])
		self.assertEqual(captured['kwargs']['timeout'], 25)
		self.assertEqual(captured['messages'][0].from_email, 'Waypost <noreply@example.com>')
		self.assertEqual(captured['messages'][0].to, ['recipient@example.com'])

	@override_settings(
		EMAIL_BACKEND='accounts.email_backends.DatabaseSMTPEmailBackend',
		DEFAULT_FROM_EMAIL='',
		TEST_OUTBOUND_EMAIL_OVERRIDE='sean.liu@istore-tech.com',
	)
	def test_database_email_backend_routes_all_outbound_email_to_override_recipient(self):
		smtp_settings = SystemSMTPSettings.get_solo()
		smtp_settings.from_email = 'noreply@example.com'
		smtp_settings.from_display_name = 'Waypost'
		smtp_settings.smtp_host = 'smtp.example.com'
		smtp_settings.smtp_port = 587
		smtp_settings.smtp_security = SystemSMTPSettings.ConnectionSecurity.STARTTLS
		smtp_settings.timeout = 25
		smtp_settings.is_active = True
		smtp_settings.set_password('smtp-pass-123')
		smtp_settings.save()

		captured = {}

		class DummySMTPBackend:
			def __init__(self, *args, **kwargs):
				captured['kwargs'] = kwargs

			def open(self):
				return True

			def close(self):
				return None

			def send_messages(self, email_messages):
				captured['messages'] = email_messages
				return len(email_messages)

		with patch('accounts.email_backends.SMTPEmailBackend', DummySMTPBackend):
			sent_count = send_mail(
				'SMTP Test',
				'Hello',
				None,
				['recipient@example.com'],
				fail_silently=False,
				html_message=None,
			)

		self.assertEqual(sent_count, 1)
		self.assertEqual(captured['messages'][0].to, ['sean.liu@istore-tech.com'])
		self.assertEqual(captured['messages'][0].cc, [])
		self.assertEqual(captured['messages'][0].bcc, [])
