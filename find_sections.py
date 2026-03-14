with open(r'D:\Отедел Системного Анализа\1. Calude code\projects\АИ\133-23-ГК-АИ2\133-23-ГК-АИ2_document.md', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find where ПС2/ПС3 spam ends
count = 0
for i, line in enumerate(lines[2400:], 2401):
    stripped = line.strip()
    if stripped not in ['ПС2', 'ПС3', '']:
        print(f'Line {i}: {line[:120]}')
        count += 1
        if count > 30:
            break
