#!/usr/bin/env python
"""
Debug script to check user roles and template logic
"""
import os
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'waypost.settings')
django.setup()

from accounts.models import User

print("=== User Role Debug ===")
users = User.objects.all()

for user in users:
    print(f"\nUser: {user.username}")
    print(f"  admin_role: '{user.admin_role}' (type: {type(user.admin_role)})")
    print(f"  admin_roles: {user.get_admin_role_codes()}")
    print(f"  admin_role is truthy: {bool(user.admin_role)}")
    print(f"  admin_role is None: {user.admin_role is None}")
    print(f"  admin_role is empty string: {user.admin_role == ''}")
    print(f"  get_admin_role_display(): '{user.get_admin_role_display()}'")
    print(f"  get_admin_roles_display(): '{user.get_admin_roles_display()}'")
    print(f"  get_access_scope_display(): '{user.get_access_scope_display()}'")
    print(f"  role: '{getattr(user, 'role', None)}'")
    print(f"  get_role_display(): '{user.get_role_display_name()}'")
    
    # Template logic simulation
    if user.admin_role:
        print(f"  TEMPLATE RESULT: Would show admin role: {user.get_admin_role_display()}")
    else:
        print(f"  TEMPLATE RESULT: Would show role label: {user.get_role_display_name()}")
