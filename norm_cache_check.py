import json
from datetime import datetime, timezone

db = json.load(open('D:/Отедел Системного Анализа/1. Calude code/norms_db.json', encoding='utf-8'))
norms = db.get('norms', {})

target_norms = [
    'ГОСТ Р 53301-2009',
    'СП 7.13130.2013',
    'СП 2.13130.2020',
    'СП 484.1311500.2020',
    'СП 60.13330.2020',
    'ГОСТ 21.1101-2013',
    'СП 54.13330.2022',
    'СП 61.13330.2012',
    'СП 51.13330.2011',
    'ГОСТ 21.602-2016',
    'ГОСТ Р 21.1101-2013',
    'ГОСТ 21.101-2020',
    'ГОСТ Р 21.1001-2009',
    'ГОСТ 24751-81'
]

today = datetime(2026, 3, 11, tzinfo=timezone.utc)

for n in target_norms:
    if n in norms:
        entry = norms[n]
        lv = entry.get('last_verified', '')
        try:
            lv_date = datetime.fromisoformat(lv.replace('Z', '+00:00'))
            days_old = (today - lv_date).days
        except:
            days_old = 999
        repl = entry.get('replacements', [])
        print(f"FOUND|{n}|{entry.get('status')}|days={days_old}|url={entry.get('source_url','')}|replacements={repl}")
    else:
        print(f"NOT_FOUND|{n}")
