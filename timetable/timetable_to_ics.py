"""把课表 JSON 转成可以导入手机日历的 .ics 文件。

用法：
    python timetable_to_ics.py courses.json
    python timetable_to_ics.py courses.json -o 我的课表.ics
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path


def fold(line: str) -> str:
    """按 RFC 5545 折行，保证每行不超过 75 字节。"""
    if len(line.encode("utf-8")) <= 75:
        return line
    chunks, buf, limit = [], b"", 74
    for ch in line:
        raw = ch.encode("utf-8")
        if len(buf) + len(raw) > limit:
            chunks.append(buf)
            buf, limit = b"", 73
        buf += raw
    chunks.append(buf)
    return "\r\n".join(
        [chunks[0].decode("utf-8")] + [" " + c.decode("utf-8") for c in chunks[1:]]
    )


def at(day: date, hhmm: str) -> datetime:
    hour, minute = (int(p) for p in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute)


def parse_day(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def course_days(course: dict, week1_monday: date) -> list[date]:
    weeks = course["weeks"]
    first, last = int(weeks["from"]), int(weeks["to"])
    step = int(weeks.get("step", 1))
    offset = int(course["weekday"]) - 1
    if not 1 <= int(course["weekday"]) <= 7:
        raise ValueError(f"{course['name']}：weekday 必须是 1-7")
    return [
        week1_monday + timedelta(weeks=week - 1, days=offset)
        for week in range(first, last + 1, step)
    ]


def holiday_ranges(cfg: dict) -> list[tuple[date, date]]:
    return [(parse_day(h["from"]), parse_day(h["to"])) for h in cfg.get("holidays", [])]


def in_holiday(day: date, holidays: list[tuple[date, date]]) -> bool:
    return any(start <= day <= end for start, end in holidays)


def build(cfg: dict) -> tuple[str, dict]:
    periods = {int(k): tuple(v) for k, v in cfg["periods"].items()}
    week1_monday = parse_day(cfg["week1_monday"])
    alarm = int(cfg.get("alarm_minutes", 15))
    holidays = holiday_ranges(cfg)
    dtstamp = datetime.now().strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//golden-hour//timetable//CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        fold(f"X-WR-CALNAME:{cfg.get('calendar_name', '课表')}"),
        "X-WR-TIMEZONE:Asia/Shanghai",
    ]
    stats = {}

    for index, course in enumerate(cfg["courses"], 1):
        span = [int(p) for p in course["periods"]]
        missing = [p for p in span if p not in periods]
        if missing:
            raise ValueError(f"{course['name']}：periods 里没有第 {missing} 节的时间")
        days = course_days(course, week1_monday)
        skipped = [d for d in days if in_holiday(d, holidays)]
        start_hm, end_hm = periods[span[0]][0], periods[span[-1]][1]
        step = int(course["weeks"].get("step", 1))

        event = [
            "BEGIN:VEVENT",
            f"UID:course-{index}@golden-hour",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART:{at(days[0], start_hm):%Y%m%dT%H%M%S}",
            f"DTEND:{at(days[0], end_hm):%Y%m%dT%H%M%S}",
            f"RRULE:FREQ=WEEKLY;COUNT={len(days)}" + (f";INTERVAL={step}" if step > 1 else ""),
        ]
        if skipped:
            event.append("EXDATE:" + ",".join(f"{at(d, start_hm):%Y%m%dT%H%M%S}" for d in skipped))

        room = course.get("room", "")
        title = f"{course['name']}（{room}）" if room else course["name"]
        detail = "；".join(
            part
            for part in (
                course.get("teacher", ""),
                f"第{span[0]}-{span[-1]}节" if len(span) > 1 else f"第{span[0]}节",
                f"第{course['weeks']['from']}-{course['weeks']['to']}周"
                + ("单周" if step == 2 and int(course["weeks"]["from"]) % 2 else ""),
            )
            if part
        )
        event.append(fold(f"SUMMARY:{title}"))
        if room:
            event.append(fold(f"LOCATION:{room}"))
        event.append(fold(f"DESCRIPTION:{detail}"))
        if alarm:
            event += [
                "BEGIN:VALARM",
                f"TRIGGER:-PT{alarm}M",
                "ACTION:DISPLAY",
                fold(f"DESCRIPTION:{alarm} 分钟后上课：{course['name']}"),
                "END:VALARM",
            ]
        event.append("END:VEVENT")
        lines += event
        stats[title] = stats.get(title, 0) + len(days) - len(skipped)

    for index, makeup in enumerate(cfg.get("makeups", []), 1):
        start, end = datetime.fromisoformat(makeup["start"]), datetime.fromisoformat(makeup["end"])
        room = makeup.get("room", "")
        title = f"{makeup['name']}（{room}·补课）" if room else f"{makeup['name']}（补课）"
        event = [
            "BEGIN:VEVENT",
            f"UID:makeup-{index}@golden-hour",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART:{start:%Y%m%dT%H%M%S}",
            f"DTEND:{end:%Y%m%dT%H%M%S}",
            fold(f"SUMMARY:{title}"),
        ]
        if room:
            event.append(fold(f"LOCATION:{room}"))
        if makeup.get("note"):
            event.append(fold(f"DESCRIPTION:{makeup['note']}"))
        if alarm:
            event += [
                "BEGIN:VALARM",
                f"TRIGGER:-PT{alarm}M",
                "ACTION:DISPLAY",
                fold(f"DESCRIPTION:{alarm} 分钟后上课：{makeup['name']}"),
                "END:VALARM",
            ]
        event.append("END:VEVENT")
        lines += event
        stats[title] = stats.get(title, 0) + 1

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n", stats


def main() -> None:
    parser = argparse.ArgumentParser(description="课表 JSON 转 .ics 日历")
    parser.add_argument("config", type=Path, help="课表配置文件")
    parser.add_argument("-o", "--output", type=Path, help="输出的 .ics 文件")
    args = parser.parse_args()

    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    payload, stats = build(cfg)
    output = args.output or args.config.with_suffix(".ics")
    output.write_text(payload, encoding="utf-8", newline="")
    print(f"已生成 {output}（{output.stat().st_size} 字节）")
    for title, count in stats.items():
        print(f"  {title:<40} {count:>3} 次")


if __name__ == "__main__":
    main()
