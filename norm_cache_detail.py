import json
from datetime import datetime, timezone

db = json.load(open('D:/Отедел Системного Анализа/1. Calude code/norms_db.json', encoding='utf-8'))
norms = db.get('norms', {})

cutoff = datetime(2026, 2, 11, tzinfo=timezone.utc)

targets = [
    'СП 17.13330.2017', 'ГОСТ Р 21.101-2020', 'ГОСТ 21.101-2020',
    'СП 50.13330.2012', 'СП 61.13330.2022', 'ГОСТ 31173-2016',
    'СП 59.13330.2020', 'ГОСТ 21.1101-2013', 'СП 1.13130.2020',
    'СП 48.13330.2019', 'СНиП 12-01-2004', 'ГОСТ 7798-70',
    'СП 30.13330.2020', 'СП 7.13330.2017', 'ГОСТ Р 21.1101-2013',
    'ГОСТ 24045-2016', 'ГОСТ 21.501-2018', 'СП 54.13330.2022',
    'СП 17.13330.2022', 'ГОСТ 6727-80', 'ГОСТ 15836-79',
    'СП 2.13130.2020', 'ГОСТ 12.4.238-2013', 'ГОСТ 25772-2021',
    'ГОСТ Р МЭК 62305', 'СП 256.1325800.2016', 'СО 153-34.21.122-2003',
    'СП 293.1325800.2017'
]

for n in targets:
    if n in norms:
        e = norms[n]
        lv = e.get('last_verified', '')
        try:
            lv_date = datetime.fromisoformat(lv.replace('Z', '+00:00'))
            if lv_date.tzinfo is None:
                lv_date = lv_date.replace(tzinfo=timezone.utc)
            fresh = lv_date >= cutoff
        except:
            fresh = False
        status = e.get('status', '?')
        cv = e.get('current_version', '') or ''
        url = e.get('source_url', '') or ''
        repl = e.get('replacement', '') or ''
        print(f"CACHE|{n}|{status}|fresh={fresh}|lv={lv[:10]}|cv={cv[:70]}|url={url[:70]}|repl={repl[:50]}")
    else:
        print(f"MISSING|{n}")
