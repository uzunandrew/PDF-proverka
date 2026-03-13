import json

f_path = 'D:/Отедел Системного Анализа/1. Calude code/projects/133-23-ГК-ОВ2.2 (7)/_output/03_findings.json'
findings_data = json.load(open(f_path, encoding='utf-8'))

items = findings_data
if isinstance(items, dict):
    items = items.get('findings', list(items.values()))

sev_order = {'КРИТИЧЕСКОЕ': 0, 'ЭКОНОМИЧЕСКОЕ': 1, 'ЭКСПЛУАТАЦИОННОЕ': 2, 'РЕКОМЕНДАТЕЛЬНОЕ': 3}

need_verify = []
for item in items:
    if not isinstance(item, dict):
        continue
    nc = item.get('norm_confidence', 1.0)
    nq = item.get('norm_quote')
    fid = item.get('id', '')
    if nc is None:
        nc = 1.0
    if nc < 0.8 or nq is None:
        need_verify.append(item)

need_verify.sort(key=lambda x: (sev_order.get(x.get('severity',''), 9), x.get('id','')))

print(f"Total findings needing quote verification: {len(need_verify)}")
for item in need_verify:
    print(f"\n--- {item.get('id')} ({item.get('severity')}) conf={item.get('norm_confidence')} ---")
    print(f"norm: {(item.get('norm') or '')[:120]}")
    nq = item.get('norm_quote')
    if nq:
        print(f"quote: {nq[:150]}")
    else:
        print(f"quote: NULL")
