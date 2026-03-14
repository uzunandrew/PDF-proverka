with open(r'D:\Отедел Системного Анализа\1. Calude code\projects\АИ\133-23-ГК-АИ2\133-23-ГК-АИ2_document.md', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find СТРАНИЦА sections after line 6200
count = 0
for i, line in enumerate(lines[6200:], 6201):
    if '## СТРАНИЦА' in line or '### BLOCK [TEXT]' in line:
        print(f'Line {i}: {line[:120]}')
        count += 1
        if count > 30:
            break
