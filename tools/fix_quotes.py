#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 interp.json 中字符串值内的 ASCII 双引号替换为中文「」。
   仅处理 note / background / analysis 三种键的行。"""
import re
from pathlib import Path

src = Path(__file__).resolve().parent.parent / 'content' / 'interp.json'
text = src.read_text(encoding='utf-8')

lines = text.split('\n')
out = []
key_re = re.compile(r'^(\s*)"(note|background|analysis)"\s*:\s*"(.*)"(,?)\s*$')
for line in lines:
    m = key_re.match(line)
    if not m:
        out.append(line)
        continue
    indent, key, body, tail = m.group(1), m.group(2), m.group(3), m.group(4)
    # 替换 body 内未转义的 " 为 「」 交替
    new_body = []
    open_q = True
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == '\\' and i + 1 < len(body):
            new_body.append(ch + body[i + 1])
            i += 2
            continue
        if ch == '"':
            new_body.append('「' if open_q else '」')
            open_q = not open_q
        else:
            new_body.append(ch)
        i += 1
    out.append(f'{indent}"{key}": "{"".join(new_body)}"{tail}')

src.write_text('\n'.join(out), encoding='utf-8')
print('done')
