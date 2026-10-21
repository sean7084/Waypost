"""Report what the RFQ LLM cost this month, and what the pre-gate kept from costing anything.

This is the operator-facing alert surface for the RFQ spend: the budget guard in
`accounts/llm_client.py` stops calls once the ceiling is reached, and without a
number nobody notices it stopped. It is deliberately a command rather than a key
in `/healthz/` - there is no notification channel yet (issue #22), so a healthz
figure would be read by nobody except an attacker, and it would put a monthly
database aggregate onto a public unauthenticated endpoint.

Usage:
  python manage.py rfq_llm_usage
  python manage.py rfq_llm_usage --month 2026-10 --by-mailbox
  python manage.py rfq_llm_usage --json
"""
import json
from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Q, Sum
from django.utils import timezone

from accounts import llm_client
from accounts.models import LlmCallRecord

NO_MAILBOX = '(mailbox not recorded)'


def _month_window(month):
    """Return the half-open (start, end) window for a 'YYYY-MM' string."""
    try:
        start = datetime.strptime(month, '%Y-%m')
    except ValueError as exc:
        raise CommandError(f'--month must look like YYYY-MM, not {month!r}.') from exc
    start = timezone.make_aware(start, timezone.get_current_timezone())
    return start, (start + timedelta(days=32)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _counts(queryset):
    return queryset.annotate(
        calls=Count('id', filter=Q(status=LlmCallRecord.Status.OK)),
        errors=Count('id', filter=Q(status=LlmCallRecord.Status.ERROR)),
        skipped=Count('id', filter=Q(status=LlmCallRecord.Status.SKIPPED)),
        budget_exceeded=Count('id', filter=Q(status=LlmCallRecord.Status.BUDGET_EXCEEDED)),
        not_configured=Count('id', filter=Q(status=LlmCallRecord.Status.NOT_CONFIGURED)),
        input_total=Sum('input_tokens'),
        output_total=Sum('output_tokens'),
    )


class Command(BaseCommand):
    help = 'Report RFQ LLM call counts, token usage, estimated CNY cost and budget use.'

    def add_arguments(self, parser):
        parser.add_argument('--month', help='Month to report as YYYY-MM (default: the current one)')
        parser.add_argument('--by-mailbox', action='store_true',
                            help='Also break the totals down per mailbox')
        parser.add_argument('--json', action='store_true', help='Emit machine-readable JSON instead of text')

    def _rows(self, queryset, field, label_when_missing=None):
        rows = []
        grouped = _counts(queryset.values(field).order_by(field))
        for row in grouped:
            value = row[field]
            label = value if value is not None else (label_when_missing or '')
            rows.append({
                'group': str(label),
                'calls': row['calls'],
                'errors': row['errors'],
                'skipped': row['skipped'],
                'budget_exceeded': row['budget_exceeded'],
                'not_configured': row['not_configured'],
                'input_tokens': row['input_total'] or 0,
                'output_tokens': row['output_total'] or 0,
                'estimated_cny': str(llm_client.cost_cny(row['input_total'], row['output_total']).quantize(Decimal('0.0001'))),
            })
        return rows

    def _write_rows(self, title, rows):
        self.stdout.write(title)
        for row in rows:
            self.stdout.write(
                f"  {row['group']:<24} calls {row['calls']:>4}  skipped {row['skipped']:>4}  "
                f"errors {row['errors']:>3}  budget_exceeded {row['budget_exceeded']:>3}  "
                f"not_configured {row['not_configured']:>3}"
            )
            self.stdout.write(
                f"  {'':<24} tokens {row['input_tokens']} in / {row['output_tokens']} out"
                f"  ~ CNY {row['estimated_cny']}"
            )
        if not rows:
            self.stdout.write('  no records')

    def handle(self, *args, **options):
        month = options['month'] or timezone.now().strftime('%Y-%m')
        start, end = _month_window(month)
        queryset = LlmCallRecord.objects.filter(created_at__gte=start, created_at__lt=end)

        by_purpose = self._rows(queryset, 'purpose')
        by_mailbox = self._rows(queryset, 'mailbox__email_address', NO_MAILBOX) if options['by_mailbox'] else []
        spend = llm_client.spend_cny(start, end).quantize(Decimal('0.0001'))
        budget = Decimal(str(settings.RFQ_LLM_MONTHLY_BUDGET_CNY))
        if budget > 0:
            budget_line = f'estimated spend CNY {spend} of budget CNY {budget} ({(spend / budget * 100).quantize(Decimal("0.1"))}%)'
        else:
            budget_line = f'estimated spend CNY {spend}; budget is 0, so calls are not capped'

        if options['json']:
            self.stdout.write(json.dumps({
                'month': month,
                'estimated_spend_cny': str(spend),
                'monthly_budget_cny': str(budget),
                'budget_line': budget_line,
                'price_per_mtok_input': settings.RFQ_LLM_PRICE_PER_MTOK_INPUT,
                'price_per_mtok_output': settings.RFQ_LLM_PRICE_PER_MTOK_OUTPUT,
                'by_purpose': by_purpose,
                'by_mailbox': by_mailbox,
            }, ensure_ascii=False, indent=2))
            return

        self.stdout.write(f'RFQ LLM usage for {month} ({settings.TIME_ZONE})')
        self._write_rows('by purpose:', by_purpose)
        if by_mailbox:
            self._write_rows('by mailbox:', by_mailbox)
        self.stdout.write(budget_line)
