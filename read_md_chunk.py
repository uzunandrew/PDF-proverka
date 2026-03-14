import sys

md_file = r'D:\Отедел Системного Анализа\1. Calude code\projects\АИ\133-23-ГК-АИ2\133-23-ГК-АИ2_document.md'

start = int(sys.argv[1]) if len(sys.argv) > 1 else 1
end = int(sys.argv[2]) if len(sys.argv) > 2 else start + 500

with open(md_file, 'r', encoding='utf-8') as f:
    lines = f.readlines()

output = ''
for i, line in enumerate(lines[start-1:end-1], start):
    output += f'{i}: {line}'

# Print in chunks to avoid output limit issues
print(output[:28000])
