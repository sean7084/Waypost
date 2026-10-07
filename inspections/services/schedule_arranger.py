"""Pure date/slot arrangement for inspection batches.

Two entry points, both dependency-free (no DB access) so they are trivially
unit-testable:

:func:`arrange`
    For schedule rows with **no** ``inspection_date``. The rule (product
    specified):

    - Cluster sites by **City**; cities are laid out as contiguous blocks across
      the batch date range so a city's sites fall within a span of days
      (minimizes travel).
    - Within a city, sites sharing the same normalized **Address / mall** are
      placed on **consecutive AM/PM slots**, so co-located stores are visited
      back-to-back.
    - Capacity is one site per slot and ``slots_per_day`` slots per day (default
      2: AM + PM). If the range cannot fit every site,
      :class:`ScheduleCapacityError` is raised so the caller can widen the
      window instead of silently over-packing.

:func:`assign_slots_by_date`
    For rows whose date **is** given but whose slot is not. Sites sharing a date
    are split as evenly as possible across that day's slots, and the sites with
    more devices get the earlier slots (AM first). More sites than slots on one
    date is allowed - several stores then share a slot, which the dashboard grid
    already renders as a list of cards.

Both prefer the busier stores in the morning: a device count may come from the
master asset list or from the schedule's own ``device_count`` column.
"""
from collections import OrderedDict
from datetime import timedelta

# Slot labels mirror ``StoreInspection.Slot`` ('am' / 'pm'). They are ordered
# earliest-first, and 'am' < 'pm' also holds lexicographically.
SLOT_LABELS = ('am', 'pm')

_SLOT_ALIASES = {
    'am': 'am', 'morning': 'am', '上午': 'am', '上半天': 'am', 'a': 'am',
    'pm': 'pm', 'afternoon': 'pm', '下午': 'pm', '下半天': 'pm', 'p': 'pm',
}


class ScheduleCapacityError(ValueError):
    """Raised when the date range cannot accommodate every site at the given capacity."""


def _norm(value):
    """Normalize a clustering key: collapse whitespace, casefold, empty -> '_'."""
    text = ' '.join(str(value or '').split()).casefold()
    return text or '_'


def as_int(value):
    """Best-effort int for a count that may arrive as text, float, or blank."""
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def parse_slot(value):
    """Normalize a schedule cell to ``'am'``/``'pm'``, or None when unspecified.

    Accepts the usual spellings (``AM``, ``p.m.``, ``上午``, ``afternoon``) so a
    schedule that does indicate the slot is honoured instead of re-balanced.
    """
    if value is None:
        return None
    text = ' '.join(str(value).split()).casefold()
    if not text:
        return None
    return _SLOT_ALIASES.get(text.replace('.', '')) or _SLOT_ALIASES.get(text)


def _by_device_count(rows, indexes):
    """Order row indexes by device count (descending), keeping input order on ties."""
    return sorted(indexes, key=lambda index: (-as_int(rows[index].get('device_count')), index))


def build_slot_sequence(start_date, end_date, slots_per_day=2):
    """Return the ordered [(date, slot)] grid for a date range."""
    labels = SLOT_LABELS[:slots_per_day]
    days = (end_date - start_date).days + 1
    if days <= 0:
        raise ScheduleCapacityError('end_date must be on or after start_date.')
    return [
        (start_date + timedelta(days=day), labels[slot_index])
        for day in range(days)
        for slot_index in range(len(labels))
    ]


def arrange(rows, start_date, end_date, slots_per_day=2):
    """Annotate each schedule row with an ``inspection_date`` and ``slot``.

    Args:
        rows: list of dicts; each should carry ``city`` and ``address`` keys
            (missing/blank values fall back to a single '_' cluster) and may
            carry ``device_count`` to influence which site gets the morning.
        start_date / end_date: the batch scheduling window.
        slots_per_day: slots per day (default 2 = AM + PM).

    Returns:
        A new list of dicts (same order as ``rows``) each augmented with
        ``inspection_date`` (datetime.date) and ``slot`` ('am'/'pm').

    Raises:
        ScheduleCapacityError: when len(rows) exceeds available slots.
    """
    if not rows:
        return []

    slot_seq = build_slot_sequence(start_date, end_date, slots_per_day)
    if len(rows) > len(slot_seq):
        raise ScheduleCapacityError(
            f'{len(rows)} sites do not fit in {(end_date - start_date).days + 1} day(s) '
            f'x {slots_per_day} slot(s) = {len(slot_seq)} slot(s). Widen the date range.'
        )

    # Group row indexes by city, preserving first-seen city order.
    city_groups = OrderedDict()
    for index, row in enumerate(rows):
        city_groups.setdefault(_norm(row.get('city')), []).append(index)

    assignments = [None] * len(rows)
    cursor = 0
    for city_indexes in city_groups.values():
        # Within a city, group by normalized address so co-located sites are consecutive.
        address_groups = OrderedDict()
        for index in city_indexes:
            address_groups.setdefault(_norm(rows[index].get('address')), []).append(index)
        for group in address_groups.values():
            for index in group:
                date, slot = slot_seq[cursor]
                cursor += 1
                assignments[index] = (date, slot)

    # Hand each date's slots out in descending device-count order so the busier
    # store of a pair gets the morning, without changing which dates are used.
    dates = OrderedDict()
    for index, (date, _slot) in enumerate(assignments):
        dates.setdefault(date, []).append(index)
    for date, indexes in dates.items():
        slots_in_use = sorted(assignments[index][1] for index in indexes)
        for index, slot in zip(_by_device_count(rows, indexes), slots_in_use):
            assignments[index] = (date, slot)

    return [
        {**row, 'inspection_date': assignments[i][0], 'slot': assignments[i][1]}
        for i, row in enumerate(rows)
    ]


def assign_slots_by_date(rows, slots_per_day=2):
    """Return a slot for every row, balancing the sites that share a date.

    Args:
        rows: list of dicts carrying ``inspection_date`` and optionally ``slot``
            (an explicit slot always wins) and ``device_count``.
        slots_per_day: slots available per day (default 2 = AM + PM).

    Returns:
        A list of slot labels, index-aligned with ``rows``. Rows with neither a
        date nor an explicit slot fall back to the first slot ('am').

    Per date the slots are filled so the day's totals are as even as possible,
    counting rows that already pinned a slot as occupying capacity - an explicit
    AM therefore pushes a blank neighbour into the PM. The pending rows are
    considered busiest-first, so the stores with more devices get the earlier
    slots. When a date holds more sites than there are slots, the surplus shares
    the slots rather than being dropped or moved: the date was appointed by the
    schedule, so it is not ours to change.
    """
    labels = list(SLOT_LABELS[:slots_per_day] or SLOT_LABELS)
    slots = [parse_slot(row.get('slot')) for row in rows]

    by_date = OrderedDict()
    for index, row in enumerate(rows):
        if row.get('inspection_date') is None:
            continue
        by_date.setdefault(row['inspection_date'], []).append(index)

    for indexes in by_date.values():
        pending = [index for index in indexes if slots[index] is None]
        if not pending:
            continue
        pinned = {label: 0 for label in labels}
        for index in indexes:
            if slots[index] in pinned:
                pinned[slots[index]] += 1

        # Even final totals per slot, earlier slots taking the odd remainder.
        base, extra = divmod(len(indexes), len(labels))
        room = {
            label: max(0, base + (1 if position < extra else 0) - pinned[label])
            for position, label in enumerate(labels)
        }
        load = dict(pinned)
        for index in _by_device_count(rows, pending):
            label = next((candidate for candidate in labels if room[candidate] > 0), None)
            if label is None:
                # Pinned rows already exceed the even split: use the least-loaded
                # slot so no site is dropped.
                label = min(labels, key=lambda candidate: (load[candidate], labels.index(candidate)))
            else:
                room[label] -= 1
            load[label] += 1
            slots[index] = label

    return [slot or labels[0] for slot in slots]
