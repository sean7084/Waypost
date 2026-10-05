#!/usr/bin/env python
"""
Manual compilation of translation files without gettext tools

Deliberately does NOT call django.setup(): compiling .po -> .mo only needs
polib, and pulling in the app registry imports quotations.services -> WeasyPrint,
which requires the native GTK runtime. Translation compilation should work on a
plain venv without any DLL setup.
"""
import os

# Simple manual compilation
import polib

def compile_po_to_mo(po_path, mo_path):
    """Convert .po file to .mo file"""
    try:
        po = polib.pofile(po_path)
        po.save_as_mofile(mo_path)
        print(f"Compiled {po_path} -> {mo_path}")
        return True
    except Exception as e:
        print(f"Error compiling {po_path}: {e}")
        return False

# Try to install polib first
try:
    import polib
except ImportError:
    print("Installing polib...")
    import subprocess
    subprocess.check_call(['pip', 'install', 'polib'])
    import polib

# Compile English
en_po = 'locale/en/LC_MESSAGES/django.po'
en_mo = 'locale/en/LC_MESSAGES/django.mo'
compile_po_to_mo(en_po, en_mo)

# Compile Chinese (canonical gettext locale path)
zh_po = 'locale/zh_CN/LC_MESSAGES/django.po'
zh_mo = 'locale/zh_CN/LC_MESSAGES/django.mo'

# Backward-compatible fallback source if older folder is still present
if not os.path.exists(zh_po):
    legacy_zh_po = 'locale/zh-cn/LC_MESSAGES/django.po'
    if os.path.exists(legacy_zh_po):
        zh_po = legacy_zh_po

compile_po_to_mo(zh_po, zh_mo)

print("Translation compilation completed!")
