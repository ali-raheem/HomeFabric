"""Recurrence and date semantics: A06 plus schedule edit rules (spec §7)."""

import datetime as dt

import pytest

from homefabric.core.dates import add_interval, local_to_utc
from homefabric.core.errors import DomainError
from homefabric.maintenance import recurrence, services
from homefabric.maintenance.models import MaintenanceSchedule, Task, TaskStatus

pytestmark = pytest.mark.django_db
D = dt.date


def test_monthly_31st_clamps_and_returns():
    anchor = D(2025, 1, 31)
    dates = [recurrence.fixed_occurrence(anchor, i, 1, "month") for i in range(5)]
    assert dates == [D(2025, 1, 31), D(2025, 2, 28), D(2025, 3, 31), D(2025, 4, 30),
                     D(2025, 5, 31)]
    assert recurrence.fixed_occurrence(D(2024, 1, 31), 1, 1, "month") == D(2024, 2, 29)


def test_leap_day_annual():
    anchor = D(2024, 2, 29)
    assert [recurrence.fixed_occurrence(anchor, i, 1, "year") for i in range(5)] == [
        D(2024, 2, 29), D(2025, 2, 28), D(2026, 2, 28), D(2027, 2, 28), D(2028, 2, 29)]


def test_weeks_days_and_multi_month():
    assert add_interval(D(2025, 1, 1), 2, "week") == D(2025, 1, 15)
    assert add_interval(D(2025, 1, 1), 10, "day") == D(2025, 1, 11)
    assert add_interval(D(2025, 8, 31), 6, "month") == D(2026, 2, 28)


def test_first_index_on_or_after():
    anchor = D(2020, 1, 31)
    idx = recurrence.first_index_on_or_after(anchor, 1, "month", D(2025, 3, 1))
    assert recurrence.fixed_occurrence(anchor, idx, 1, "month") == D(2025, 3, 31)
    assert recurrence.fixed_occurrence(anchor, idx - 1, 1, "month") < D(2025, 3, 1)
    assert recurrence.first_index_on_or_after(D(2025, 1, 1), 1, "day", D(2024, 1, 1)) == 0


def test_uk_dst_nonexistent_time_moves_forward():
    # 30 March 2025: clocks go 01:00 -> 02:00 in London; 01:30 does not exist.
    utc = local_to_utc(D(2025, 3, 30), dt.time(1, 30), "Europe/London")
    assert utc == dt.datetime(2025, 3, 30, 1, 0, tzinfo=dt.UTC)  # 02:00 BST


def test_uk_dst_ambiguous_time_fires_once_first_occurrence():
    # 26 October 2025: 01:30 happens twice in London; use the first (BST).
    utc = local_to_utc(D(2025, 10, 26), dt.time(1, 30), "Europe/London")
    assert utc == dt.datetime(2025, 10, 26, 0, 30, tzinfo=dt.UTC)


def test_reminder_0900_across_dst():
    assert local_to_utc(D(2025, 7, 1), dt.time(9), "Europe/London").hour == 8
    assert local_to_utc(D(2025, 12, 1), dt.time(9), "Europe/London").hour == 9


def _schedule(entity, owner, **kw):
    params = dict(title="Service", mode="fixed", interval_count=1, interval_unit="month",
                  anchor_date=D(2025, 1, 31), generate=False)
    params.update(kw)
    return services.create_schedule(entity, actor=owner, **params)


def test_fixed_generation_horizon_and_idempotence(home, owner):
    s = _schedule(home["boiler"], owner)
    created = services.generate_for_schedule(s, today=D(2025, 1, 15))
    # 90-day horizon from 15 Jan ends 15 Apr.
    assert [t.due_date for t in created] == [D(2025, 1, 31), D(2025, 2, 28), D(2025, 3, 31)]
    assert services.generate_for_schedule(s, today=D(2025, 1, 15)) == []
    assert Task.objects.filter(schedule=s).count() == 3


def test_downtime_generates_missed_as_overdue(home, owner):
    s = _schedule(home["boiler"], owner, anchor_date=D(2025, 1, 1))
    created = services.generate_for_schedule(s, today=D(2025, 4, 10))
    overdue = [t for t in created if t.generated_as_overdue]
    assert [t.due_date for t in overdue] == [D(2025, 1, 1), D(2025, 2, 1), D(2025, 3, 1),
                                             D(2025, 4, 1)]
    assert all(t.status == TaskStatus.OPEN for t in overdue)


def test_fixed_late_completion_does_not_move_schedule(home, owner):
    s = _schedule(home["boiler"], owner, interval_unit="year", anchor_date=D(2025, 3, 1))
    services.generate_for_schedule(s, today=D(2025, 2, 1))
    first = Task.objects.get(schedule=s, due_date=D(2025, 3, 1))
    services.complete_task(first, actor=owner, expected_version=first.version,
                           completed_on=D(2025, 4, 20))  # seven weeks late
    services.generate_for_schedule(s, today=D(2026, 1, 15))
    assert Task.objects.filter(schedule=s, due_date=D(2026, 3, 1)).exists()
    assert not Task.objects.filter(schedule=s, due_date=D(2026, 4, 20)).exists()


def test_after_completion_next_due_from_actual_date(home, owner):
    s = _schedule(home["filter"], owner, mode="after_completion", interval_count=6,
                  anchor_date=D(2025, 1, 10))
    services.generate_for_schedule(s, today=D(2025, 1, 1))
    task = Task.objects.get(schedule=s)
    services.complete_task(task, actor=owner, expected_version=task.version,
                           completed_on=D(2025, 2, 3))
    nxt = Task.objects.get(schedule=s, status=TaskStatus.OPEN)
    assert nxt.due_date == D(2025, 8, 3)
    assert Task.objects.filter(schedule=s, status__in=["open", "in_progress"]).count() == 1


def test_after_completion_backdated_completion_can_be_overdue(home, owner):
    s = _schedule(home["filter"], owner, mode="after_completion", interval_count=1,
                  interval_unit="month", anchor_date=D(2024, 1, 1))
    services.generate_for_schedule(s, today=D(2024, 1, 1))
    task = Task.objects.get(schedule=s)
    services.complete_task(task, actor=owner, expected_version=task.version,
                           completed_on=D(2024, 1, 5))
    nxt = Task.objects.get(schedule=s, status=TaskStatus.OPEN)
    assert nxt.due_date == D(2024, 2, 5)  # already overdue relative to today, shown as such
    assert nxt.is_overdue(dt.date.today())
    # No fictional completions were created to catch up.
    assert Task.objects.filter(schedule=s, status=TaskStatus.COMPLETED).count() == 1


def test_after_completion_skip_requires_new_date_or_pause(home, owner):
    s = _schedule(home["filter"], owner, mode="after_completion", anchor_date=D(2025, 1, 10))
    services.generate_for_schedule(s, today=D(2025, 1, 1))
    task = Task.objects.get(schedule=s)
    with pytest.raises(DomainError, match="next due date or pause"):
        services.skip_task(task, actor=owner, expected_version=task.version, reason="Away")
    task.refresh_from_db()
    assert task.status == TaskStatus.OPEN  # nothing partially applied
    services.skip_task(task, actor=owner, expected_version=task.version, reason="Away",
                       new_due_date=D(2025, 3, 1))
    assert Task.objects.get(schedule=s, status=TaskStatus.OPEN).due_date == D(2025, 3, 1)
    nxt = Task.objects.get(schedule=s, status=TaskStatus.OPEN)
    services.skip_task(nxt, actor=owner, expected_version=nxt.version, reason="Selling",
                       pause_schedule=True)
    assert not MaintenanceSchedule.objects.get(pk=s.pk).enabled


def test_rescheduling_one_occurrence_keeps_anchor(home, owner):
    s = _schedule(home["boiler"], owner)
    services.generate_for_schedule(s, today=D(2025, 1, 15))
    feb = Task.objects.get(schedule=s, due_date=D(2025, 2, 28))
    services.update_task(feb, actor=owner, expected_version=feb.version, title=feb.title,
                         description="", due_date=D(2025, 3, 10), assignee=None)
    s.refresh_from_db()
    assert s.anchor_date == D(2025, 1, 31)
    services.generate_for_schedule(s, today=D(2025, 3, 15))
    assert Task.objects.filter(schedule=s, scheduled_date=D(2025, 5, 31)).exists()


def test_schedule_edit_affects_only_unstarted_future(home, owner):
    s = _schedule(home["boiler"], owner, anchor_date=D(2025, 1, 1))
    services.generate_for_schedule(s, today=D(2025, 1, 1))
    jan = Task.objects.get(schedule=s, due_date=D(2025, 1, 1))
    services.complete_task(jan, actor=owner, expected_version=jan.version,
                           completed_on=D(2025, 1, 1))
    feb = Task.objects.get(schedule=s, due_date=D(2025, 2, 1))
    services.start_task(feb, actor=owner, expected_version=feb.version)
    preview = services.preview_schedule_change(s, data={"anchor_date": D(2025, 3, 15)},
                                               today=D(2025, 1, 20))
    assert [t.due_date for t in preview["affected"]] == [D(2025, 3, 1), D(2025, 4, 1)]
    s.refresh_from_db()
    services.update_schedule(s, actor=owner, expected_version=s.version,
                             data={"anchor_date": D(2025, 3, 15)}, today=D(2025, 1, 20))
    dues = sorted(Task.objects.filter(schedule=s).values_list("due_date", flat=True))
    assert D(2025, 1, 1) in dues and D(2025, 2, 1) in dues  # completed + in progress kept
    assert D(2025, 3, 1) not in dues and D(2025, 3, 15) in dues


def test_disable_and_reenable_with_catch_up_preview(home, owner):
    s = _schedule(home["boiler"], owner, anchor_date=D(2025, 1, 1), horizon_days=10)
    services.generate_for_schedule(s, today=D(2025, 1, 1))
    services.set_schedule_enabled(s, False, actor=owner)
    s.refresh_from_db()
    assert services.generate_for_schedule(s, today=D(2025, 6, 1)) == []
    preview = services.catch_up_preview(s, today=D(2025, 6, 1))
    assert preview["missed"] == [D(2025, 2, 1), D(2025, 3, 1), D(2025, 4, 1), D(2025, 5, 1)]
    created = services.set_schedule_enabled(s, True, actor=owner, skip_missed=True,
                                            today=D(2025, 6, 1))
    assert [t.due_date for t in created] == [D(2025, 6, 1)]


def test_bulk_skip_with_reason(home, owner):
    s = _schedule(home["boiler"], owner, interval_unit="week", anchor_date=D(2025, 1, 1))
    services.generate_for_schedule(s, today=D(2025, 2, 1))
    overdue = Task.objects.filter(schedule=s, due_date__lt=D(2025, 2, 1))
    n = services.bulk_skip(list(overdue), actor=owner, reason="On holiday")
    assert n == 5
    assert Task.objects.filter(schedule=s, status=TaskStatus.SKIPPED).count() == 5
    assert home["boiler"].activity.filter(event_type="task_skipped").count() == 5
    assert not home["boiler"].activity.filter(event_type="service").exists()


def test_concurrent_generation_creates_no_duplicates(home, owner):
    s = _schedule(home["boiler"], owner, interval_unit="day", anchor_date=D(2025, 1, 1))
    services.generate_for_schedule(s, today=D(2025, 1, 1))
    # A stale second worker (next_index reset) must not duplicate occurrences.
    MaintenanceSchedule.objects.filter(pk=s.pk).update(next_index=0)
    services.generate_for_schedule(s, today=D(2025, 1, 1))
    keys = list(Task.objects.filter(schedule=s).values_list("occurrence_key", flat=True))
    assert len(keys) == len(set(keys)) == 91


def test_preview_dates():
    assert services.preview_dates(mode="fixed", anchor_date=D(2025, 1, 31), interval_count=1,
                                  interval_unit="month", count=3) == [
        D(2025, 1, 31), D(2025, 2, 28), D(2025, 3, 31)]
    assert services.preview_dates(mode="after_completion", anchor_date=D(2025, 1, 1),
                                  interval_count=6, interval_unit="month", count=2) == [
        D(2025, 1, 1), D(2025, 7, 1)]


def test_household_timezone_change_keeps_calendar_dates(household, home, owner):
    from homefabric.accounts.services import update_household

    task = services.create_task(home["boiler"], actor=owner, title="X", due_date=D(2030, 5, 1))
    household.refresh_from_db()
    update_household(household, expected_version=household.version, actor=owner,
                     name=household.name, timezone_name="Pacific/Auckland", currency="GBP")
    task.refresh_from_db()
    assert task.due_date == D(2030, 5, 1)
