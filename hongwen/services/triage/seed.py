"""Seed the shipped guided-triage playbooks (Django ORM).

Port of helpdesk/backend/app/triage/seed.py. The "no-internet" playbook and its
four screenshots are authored Simplified Chinese content, preserved verbatim.
Re-seeding upserts assets/playbooks and re-indexes steps in one transaction; an
in-flight TriageSession can point at a different step afterwards (harmless while
one playbook exists - version the playbook instead of overwriting if that changes).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from django.db import transaction

from hongwen.models import TriageAsset, TriagePlaybook, TriageStep
from hongwen.services.kb.vector_store import embed_query
from hongwen.services.triage.assets import asset_relative_path, materialize_seed_assets, read_asset_dimensions

ASSETS: List[Dict[str, Any]] = [
    {
        'asset_key': 'ios_wifi_settings',
        'filename': 'ios_wifi_settings.png',
        'caption_zh': 'iPad「设置 > 无线局域网」界面，这里能看到当前连接的 Wi-Fi 名称。',
        'source_url': '',
        'license': 'AI-generated illustration',
    },
    {
        'asset_key': 'windows_taskbar_network',
        'filename': 'windows_taskbar_network.png',
        'caption_zh': 'Windows 任务栏右下角的网络 / Wi-Fi 图标位置。',
        'source_url': '',
        'license': 'AI-generated illustration',
    },
    {
        'asset_key': 'browser_test',
        'filename': 'browser_test.png',
        'caption_zh': '在浏览器（Chrome / Edge / Safari）地址栏输入网址并打开网页。',
        'source_url': '',
        'license': 'AI-generated illustration',
    },
    {
        'asset_key': 'printer_power_network',
        'filename': 'printer_power_network.png',
        'caption_zh': '收银打印机 / AIO 一体机的电源键与网口位置。',
        'source_url': '',
        'license': 'AI-generated illustration',
    },
]

PLAYBOOKS: List[Dict[str, Any]] = [
    {
        'slug': 'no-internet',
        'title_zh': '无法上网 / 网络异常',
        'symptom_summary_zh': (
            '设备上不了网、网页打不开、Wi-Fi 连不上或连上了但没有网络、'
            '收银打印机或电脑无法联网、网络图标有感叹号或变成地球图标'
        ),
        'category': 'network',
        'steps': [
            {
                'step_index': 0, 'kind': 'intake_device',
                'prompt_zh': '先确认一下：你主要用哪种设备上网出了问题？',
                'device_scope': None, 'expected_result_zh': '', 'asset': None,
                'quick_replies': [
                    {'label_zh': 'iPad / iPhone', 'value': 'ios'},
                    {'label_zh': 'Windows 电脑', 'value': 'windows'},
                    {'label_zh': '收银打印机 / AIO', 'value': 'printer'},
                    {'label_zh': '其他设备', 'value': 'other'},
                ],
                'branch_map': {'ios': 1, 'windows': 4, 'printer': 7, 'other': 7, 'default': 7},
            },
            {
                'step_index': 1, 'kind': 'check', 'device_scope': 'ios',
                'prompt_zh': '在 iPad 上打开「设置」>「无线局域网」，看看是不是连着一个 Wi-Fi，名字后面有没有「已连接」。',
                'expected_result_zh': '能看到已连接到某个 Wi-Fi 名称', 'asset': 'ios_wifi_settings',
                'quick_replies': [
                    {'label_zh': '已连接', 'value': 'connected'},
                    {'label_zh': '没连接 / 找不到', 'value': 'none'},
                ],
                'branch_map': {'connected': 2, 'none': 3, 'default': 2},
            },
            {
                'step_index': 2, 'kind': 'check', 'device_scope': 'ios',
                'prompt_zh': '已经连着 Wi-Fi 但还是上不了网：用 Safari 打开一个网站（比如 baidu.com），能加载出来吗？',
                'expected_result_zh': '网页能正常打开', 'asset': 'browser_test',
                'quick_replies': [
                    {'label_zh': '能打开', 'value': 'ok'},
                    {'label_zh': '打不开', 'value': 'fail'},
                ],
                'branch_map': {'ok': 10, 'fail': 3, 'default': 3},
            },
            {
                'step_index': 3, 'kind': 'solution', 'device_scope': 'ios',
                'prompt_zh': (
                    '试试这几步：\n'
                    '1）「设置」>「无线局域网」> 点当前 Wi-Fi 右边的 i >「忽略此网络」，再重新连接并输入密码；\n'
                    '2）把 Wi-Fi 关掉再打开；\n'
                    '3）如果装了 GVPN / 代理类 App，先关闭它再重试。'
                ),
                'expected_result_zh': '', 'asset': None,
                'quick_replies': [
                    {'label_zh': '已解决', 'value': 'solved'},
                    {'label_zh': '还不行', 'value': 'unsolved'},
                ],
                'branch_map': {'solved': 10, 'unsolved': 9, 'default': 9},
            },
            {
                'step_index': 4, 'kind': 'check', 'device_scope': 'windows',
                'prompt_zh': '看屏幕右下角任务栏的网络图标（Wi-Fi 或小电脑图标）：是不是有感叹号，或者变成了一个地球图标？',
                'expected_result_zh': '图标正常、没有感叹号', 'asset': 'windows_taskbar_network',
                'quick_replies': [
                    {'label_zh': '正常', 'value': 'ok'},
                    {'label_zh': '有感叹号', 'value': 'warn'},
                    {'label_zh': '是地球图标', 'value': 'globe'},
                ],
                'branch_map': {'ok': 5, 'warn': 6, 'globe': 6, 'default': 6},
            },
            {
                'step_index': 5, 'kind': 'check', 'device_scope': 'windows',
                'prompt_zh': '网络图标正常的话：用 Chrome 或 Edge 打开一个网站（比如 baidu.com），能加载吗？',
                'expected_result_zh': '网页能正常打开', 'asset': 'browser_test',
                'quick_replies': [
                    {'label_zh': '能打开', 'value': 'ok'},
                    {'label_zh': '打不开', 'value': 'fail'},
                ],
                'branch_map': {'ok': 10, 'fail': 6, 'default': 6},
            },
            {
                'step_index': 6, 'kind': 'solution', 'device_scope': 'windows',
                'prompt_zh': (
                    '试试这几步：\n'
                    '1）点右下角网络图标 > 断开当前 Wi-Fi 再重新连接；\n'
                    '2）如果用的是公司 GVPN，先退出 GVPN 再打开网页；\n'
                    '3）还不行就重启电脑；\n'
                    '4）若只有这台设备不行，重启路由器 / 光猫（拔电源等 30 秒再插回）。'
                ),
                'expected_result_zh': '', 'asset': None,
                'quick_replies': [
                    {'label_zh': '已解决', 'value': 'solved'},
                    {'label_zh': '还不行', 'value': 'unsolved'},
                ],
                'branch_map': {'solved': 10, 'unsolved': 9, 'default': 9},
            },
            {
                'step_index': 7, 'kind': 'check', 'device_scope': 'printer',
                'prompt_zh': '先看收银打印机 / AIO：电源开着吗？网线两端插紧了吗？网口的灯有没有亮 / 闪？',
                'expected_result_zh': '电源灯亮、网口灯闪烁', 'asset': 'printer_power_network',
                'quick_replies': [
                    {'label_zh': '都正常', 'value': 'ok'},
                    {'label_zh': '有问题', 'value': 'bad'},
                ],
                'branch_map': {'ok': 8, 'bad': 8, 'default': 8},
            },
            {
                'step_index': 8, 'kind': 'solution', 'device_scope': 'printer',
                'prompt_zh': (
                    '试试这几步：\n'
                    '1）长按电源键关机，等 10 秒再开机；\n'
                    '2）拔下网线重新插紧，或换一个网口；\n'
                    '3）重启连接的路由器 / 交换机（拔电源等 30 秒再插回）；\n'
                    '4）开机后打印一张自检 / 网络状态页，确认已经连上网。'
                ),
                'expected_result_zh': '', 'asset': None,
                'quick_replies': [
                    {'label_zh': '已解决', 'value': 'solved'},
                    {'label_zh': '还不行', 'value': 'unsolved'},
                ],
                'branch_map': {'solved': 10, 'unsolved': 9, 'default': 9},
            },
            {
                'step_index': 9, 'kind': 'escalate', 'device_scope': None,
                'prompt_zh': '已经帮你记录本次排查结果。如果还没恢复，请联系 IT / 人工同事进一步处理（说明设备类型、已试过的步骤和现在的现象）。',
                'expected_result_zh': '', 'asset': None,
                'quick_replies': [{'label_zh': '重新开始', 'value': 'restart'}],
                'branch_map': {'restart': 0, 'default': 9},
            },
            {
                'step_index': 10, 'kind': 'end', 'device_scope': None,
                'prompt_zh': '很好，网络应该已经恢复了。如果之后又出问题，随时回来按步骤再排查一次。',
                'expected_result_zh': '', 'asset': None,
                'quick_replies': [{'label_zh': '重新开始', 'value': 'restart'}],
                'branch_map': {'restart': 0, 'default': 10},
            },
        ],
    },
]


def _upsert_asset(definition: Dict[str, Any]) -> TriageAsset:
    relative_path = asset_relative_path(definition['filename'])
    dimensions = read_asset_dimensions(relative_path)
    asset = TriageAsset.objects.filter(asset_key=definition['asset_key']).first()
    if not asset:
        asset = TriageAsset(asset_key=definition['asset_key'], relative_path=relative_path)
    asset.relative_path = relative_path
    asset.caption_zh = definition.get('caption_zh', '')
    asset.source_url = definition.get('source_url', '')
    asset.license = definition.get('license', '')
    asset.width = dimensions[0] if dimensions else 0
    asset.height = dimensions[1] if dimensions else 0
    asset.save()
    return asset


def _upsert_playbook(definition: Dict[str, Any], *, customer_id: Optional[int], asset_ids: Dict[str, int]) -> TriagePlaybook:
    slug = definition['slug']
    playbook = TriagePlaybook.objects.filter(slug=slug).first()
    if not playbook:
        playbook = TriagePlaybook(slug=slug, title_zh=definition['title_zh'], symptom_summary_zh=definition['symptom_summary_zh'])
    playbook.title_zh = definition['title_zh']
    playbook.symptom_summary_zh = definition['symptom_summary_zh']
    playbook.category = definition.get('category', '')
    playbook.customer_id = customer_id
    playbook.is_active = True
    playbook.embedding = embed_query(definition['symptom_summary_zh'])
    playbook.save()

    # Re-index steps so re-running the seed always reflects the latest content.
    TriageStep.objects.filter(playbook_id=playbook.id).delete()
    for step_def in definition['steps']:
        asset_key = step_def.get('asset')
        TriageStep.objects.create(
            playbook=playbook,
            step_index=int(step_def['step_index']),
            kind=step_def['kind'],
            prompt_zh=step_def['prompt_zh'],
            device_scope=step_def.get('device_scope'),
            expected_result_zh=step_def.get('expected_result_zh', ''),
            asset_id=asset_ids.get(asset_key) if asset_key else None,
            quick_replies_json=step_def.get('quick_replies', []),
            branch_map_json=step_def.get('branch_map', {}),
        )
    return playbook


def seed(*, customer_id: Optional[int] = None) -> None:
    """Insert or refresh the shipped playbooks in one transaction."""
    materialize_seed_assets()
    with transaction.atomic():
        asset_ids: Dict[str, int] = {}
        for definition in ASSETS:
            asset = _upsert_asset(definition)
            asset_ids[asset.asset_key] = asset.id
        for definition in PLAYBOOKS:
            _upsert_playbook(definition, customer_id=customer_id, asset_ids=asset_ids)