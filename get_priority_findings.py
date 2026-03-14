import json
with open('projects/АИ/133-23-ГК-АИ2/_output/03_findings.json','r',encoding='utf-8') as f:
    data = json.load(f)

priority = []
for finding in data['findings']:
    sev = finding['severity']
    conf = finding.get('norm_confidence', 1.0)
    quote = finding.get('norm_quote')
    norm = finding.get('norm','')
    if sev in ('КРИТИЧЕСКОЕ','ЭКОНОМИЧЕСКОЕ') and (conf < 0.8 or quote is None):
        priority.append({'id': finding['id'], 'sev': sev, 'conf': conf, 'quote': quote, 'norm': norm})

priority.sort(key=lambda x: (0 if x['sev']=='КРИТИЧЕСКОЕ' else 1, x['conf']))
for p in priority[:10]:
    q = str(p['quote'])[:60] if p['quote'] else 'null'
    print(f"{p['id']} | {p['sev']} | conf={p['conf']} | norm={str(p['norm'])[:70]}")
    print(f"  quote={q}")
