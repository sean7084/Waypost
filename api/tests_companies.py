"""Tests for the company REST surface: who may create a company, and what each
role can see.

The create path is guarded twice over, and both halves matter:
`CompanyViewSet.get_queryset()` scopes reads per role, while the explicit refusal
in `perform_create` is the only thing standing between a non-superadmin and a
write. That refusal must reach the client as a 403: it used to raise the builtin
`PermissionError`, which DRF does not translate, so a forbidden create returned a
500 (the server "succeeded" at failing).
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from companies.models import Company

User = get_user_model()


def _make_it_admin(username, company):
    """An IT administrator scoped to ``company``."""
    user = User.objects.create_user(username=username, password='pw')
    user.set_admin_roles([User.AdminRole.IT_ADMINISTRATOR])
    user.managed_company = company
    user.save(update_fields=['managed_company'])
    return user


class CompanyCreatePermissionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.own = Company.objects.create(name='Own Co', code='OWN')
        self.other = Company.objects.create(name='Other Co', code='OTH')
        self.it_admin = _make_it_admin('itadmin1', self.own)
        self.plain = User.objects.create_user(username='plain1', password='pw')
        self.superadmin = User.objects.create_superuser(
            username='root1', email='root1@example.com', password='pw',
        )

    def _payload(self, name='Brand New Co', code='NEWCO'):
        return {'name': name, 'code': code}

    def test_it_administrator_create_returns_403_not_500(self):
        self.client.force_authenticate(self.it_admin)
        res = self.client.post('/api/v1/companies/', self._payload(), format='json')
        self.assertEqual(res.status_code, 403, res.content)
        self.assertIn('superadmin', str(res.data.get('detail', '')).lower())
        self.assertFalse(Company.objects.filter(code='NEWCO').exists())

    def test_plain_user_create_returns_403_not_500(self):
        self.client.force_authenticate(self.plain)
        res = self.client.post('/api/v1/companies/', self._payload(), format='json')
        self.assertEqual(res.status_code, 403, res.content)
        self.assertFalse(Company.objects.filter(code='NEWCO').exists())

    def test_superadmin_can_create(self):
        self.client.force_authenticate(self.superadmin)
        res = self.client.post('/api/v1/companies/', self._payload(), format='json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertTrue(Company.objects.filter(code='NEWCO').exists())

    def test_it_administrator_update_of_unscoped_company_is_not_found(self):
        """Writes are scoped by get_queryset(), not by a permission class."""
        self.client.force_authenticate(self.it_admin)
        res = self.client.put(
            f'/api/v1/companies/{self.other.id}/', {'name': 'Renamed'}, format='json',
        )
        self.assertEqual(res.status_code, 404, res.content)
        self.other.refresh_from_db()
        self.assertEqual(self.other.name, 'Other Co')

    def test_it_administrator_can_update_own_scoped_company(self):
        self.client.force_authenticate(self.it_admin)
        res = self.client.patch(
            f'/api/v1/companies/{self.own.id}/', {'name': 'Renamed Own'}, format='json',
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.own.refresh_from_db()
        self.assertEqual(self.own.name, 'Renamed Own')


class CompanyScopingTests(TestCase):
    """`get_queryset()` scoping per role, unchanged by the create fix."""

    def setUp(self):
        self.client = APIClient()
        self.own = Company.objects.create(name='Own Co', code='OWN')
        self.other = Company.objects.create(name='Other Co', code='OTH')
        self.it_admin = _make_it_admin('itadmin2', self.own)
        self.superadmin = User.objects.create_superuser(
            username='root2', email='root2@example.com', password='pw',
        )

    def _listed_codes(self, user):
        self.client.force_authenticate(user)
        res = self.client.get('/api/v1/companies/')
        self.assertEqual(res.status_code, 200, res.content)
        return {row['code'] for row in res.data['results']}

    def test_it_administrator_sees_only_managed_company(self):
        self.assertEqual(self._listed_codes(self.it_admin), {'OWN'})

    def test_superadmin_sees_every_company(self):
        self.assertEqual(self._listed_codes(self.superadmin), {'OWN', 'OTH'})

    def test_unscoped_user_sees_no_company(self):
        plain = User.objects.create_user(username='plain2', password='pw')
        self.assertEqual(self._listed_codes(plain), set())
