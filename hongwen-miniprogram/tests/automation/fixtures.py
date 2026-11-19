"""Django-side fixtures for the DevTools automator harness.

Run from the Waypost repo root with the harness venv, e.g.:

    .venv/Scripts/python.exe hongwen-miniprogram/tests/automation/fixtures.py ensure-seed

The harness spawns this with PRIMARY_EMBED_MODEL pointed at the Volcengine backup
embedding model, because the primary Qwen/DashScope embedding endpoint is in
arrears (HTTP 400 Arrearage) and cannot seed/match triage playbooks. The override
is process-local; the repo .env is never modified.

Commands:
  ensure-seed                 seed triage playbooks + ensure the test customer
  reset-user                  delete the simulator's hongwen customer (new-user test)
  link-customer <external_id> bind the profile to the test customer (skip identification)
  get-profile <external_id>   print {nickname, mobile, customer_id, has_profile} as JSON
  make-image <out_path>       write a small normal-content test JPEG for upload tests
"""
import json
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, REPO_ROOT)

from waypost.runtime_setup import (  # noqa: E402
    configure_windows_fontconfig,
    configure_windows_weasyprint_runtime,
    load_local_env,
)

load_local_env()
configure_windows_weasyprint_runtime()
configure_windows_fontconfig()
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'waypost.settings')

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from accounts.models import WeChatIdentity  # noqa: E402
from hongwen.models import Customer, HongwenProfile  # noqa: E402

TEST_CUSTOMER_NAME = '泓问测试门店'


def _customer():
    customer, _ = Customer.objects.get_or_create(
        name=TEST_CUSTOMER_NAME, defaults={'notes': 'DevTools automator harness fixture'}
    )
    return customer


def ensure_seed():
    from hongwen.services.triage.seed import seed

    seed()
    from hongwen.models import TriagePlaybook, TriageStep

    customer = _customer()
    print(json.dumps({
        'ok': True,
        'customer_id': customer.id,
        'playbooks': list(TriagePlaybook.objects.values_list('slug', flat=True)),
        'steps': TriageStep.objects.count(),
        'embed_model': settings.PRIMARY_EMBED_MODEL,
    }, ensure_ascii=False))


def reset_user():
    appid = settings.WECHAT_HONGWEN_APPID
    identities = list(WeChatIdentity.objects.filter(appid=appid).select_related('user'))
    deleted = []
    for identity in identities:
        user = identity.user
        # Only remove customer accounts (hongwen profile), never staff.
        if getattr(user, 'hongwen_profile', None) is None:
            continue
        deleted.append(user.username)
        user.delete()  # cascades profile, identity, sessions, messages
    print(json.dumps({'ok': True, 'deleted': deleted}, ensure_ascii=False))


def link_customer(external_id):
    customer = _customer()
    profile = HongwenProfile.objects.filter(external_id=external_id).first()
    if profile is None:
        print(json.dumps({'ok': False, 'error': 'profile not found', 'external_id': external_id}))
        return
    profile.customer_id = customer.id
    profile.save(update_fields=['customer', 'updated_at'])
    print(json.dumps({'ok': True, 'customer_id': customer.id, 'external_id': external_id}))


def get_profile(external_id):
    profile = HongwenProfile.objects.filter(external_id=external_id).first()
    if profile is None:
        print(json.dumps({'has_profile': False}))
        return
    print(json.dumps({
        'has_profile': True,
        'nickname': profile.nickname,
        'mobile': profile.mobile,
        'customer_id': profile.customer_id,
    }, ensure_ascii=False))


def make_image(out_path):
    from PIL import Image, ImageDraw

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img = Image.new('RGB', (900, 600), (245, 247, 250))
    draw = ImageDraw.Draw(img)
    draw.rectangle([40, 40, 860, 560], outline=(30, 90, 160), width=6)
    draw.rectangle([80, 120, 820, 200], fill=(210, 225, 245))
    draw.rectangle([80, 240, 620, 320], fill=(225, 235, 250))
    draw.ellipse([660, 360, 820, 520], fill=(120, 170, 220))
    draw.text((90, 140), 'Hongwen automator test image', fill=(20, 40, 80))
    draw.text((90, 260), 'normal content for upload / preview checks', fill=(20, 40, 80))
    img.save(out_path, 'JPEG', quality=85)
    print(json.dumps({'ok': True, 'path': out_path, 'bytes': os.path.getsize(out_path)}))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'ensure-seed':
        ensure_seed()
    elif cmd == 'reset-user':
        reset_user()
    elif cmd == 'link-customer':
        link_customer(sys.argv[2])
    elif cmd == 'get-profile':
        get_profile(sys.argv[2])
    elif cmd == 'make-image':
        make_image(sys.argv[2])
    else:
        print(json.dumps({'ok': False, 'error': f'unknown command: {cmd}'}))
        sys.exit(2)


if __name__ == '__main__':
    main()