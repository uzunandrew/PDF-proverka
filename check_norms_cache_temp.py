import json
from datetime import datetime, timedelta

with open('norms_db.json', 'r', encoding='utf-8') as f:
    db = json.load(f)

norms_to_check = [
    'СП 1.13130.2020',
    'СП 154.13130.2013',
    'ФЗ-123',
    'ГОСТ 30247.0-94',
    'ГОСТ 31173-2016',
    'ГОСТ Р 57274.1-2016',
    'ГОСТ 21.501-2018',
    'СП 2.13130.2020',
    'ГОСТ 21.101-2020',
    'СП 113.13330.2016',
    'СП 71.13330.2017',
    'СП 59.13330.2020',
    'ГОСТ Р 51631-2008',
    'СП 29.13330.2011',
    'ГОСТ Р 56387-2018',
    'СП 51.13330.2011',
    'СП 256.1325800.2016',
    'СП 52.13330.2016',
    'ГОСТ 6787-2001',
    'ГОСТ 8509-2014',
    'СП 29.13330.2017',
    'ГОСТ 8509-93',
    'ГОСТ Р 55711-2013',
    'СП 7.13130.2013',
    'СП 54.13330.2022',
]

cutoff = datetime(2026, 3, 14) - timedelta(days=30)
norms = db.get('norms', {})

results = []
not_found = []
needs_web = []

for norm_id in norms_to_check:
    if norm_id in norms:
        entry = norms[norm_id]
        lv = entry.get('last_verified', '')
        if lv:
            lv_dt = datetime.fromisoformat(lv.replace('Z',''))
            fresh = lv_dt >= cutoff
        else:
            fresh = False
        results.append({'id': norm_id, 'found': True, 'fresh': fresh, 'data': entry})
        if not fresh:
            needs_web.append(norm_id)
    else:
        not_found.append(norm_id)
        results.append({'id': norm_id, 'found': False})

print('=== FOUND IN CACHE ===')
for r in results:
    if r['found']:
        e = r['data']
        print(f"  {r['id']}: status={e['status']}, fresh={r['fresh']}, last_verified={e.get('last_verified','?')[:10]}")

print()
print('=== NOT FOUND ===')
for n in not_found:
    print(f'  {n}')

print()
print('=== NEEDS WEBSEARCH (stale) ===')
for n in needs_web:
    print(f'  {n}')
