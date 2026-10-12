"""静态扫描「项目实际用到了第三方包的哪些 API」（只读，不 import 应用代码）。

**用途**：为「能不能把几个 conda 环境的 Python 版本统一」提供**代码级**证据。
依赖元数据上的版本约束常常写得很松（老包的 `Requires-Python`、`numpy>=1.17` 之类），
真正决定兼容性的是**代码里调用的 API 是否在目标版本里存在**。本脚本把这份「API 面」抽出来，
供人工逐个核对版本可用性。

方法：AST 解析全部第一方代码，抽出
  1. 每个第三方模块的 import 次数；
  2. `from X import a, b` 里的名字（按模块归类）；
  3. 形如 `mod.attr.attr` 的属性访问链 —— **这才是会随版本变动的东西**；
  4. 动态 import（`import_module` / `find_spec` / `__import__`）—— 静态分析盲区，单独列出。

用法：
    conda activate MyAI
    python tools/scan_api_surface.py                          # 扫仓库根，打印摘要
    python tools/scan_api_surface.py --watch torch,transformers
    python tools/scan_api_surface.py --out api_surface.md     # 同时写报告文件

注意：脚本本身不依赖第三方包，也不 import 应用代码，可以随时跑。
"""

from __future__ import annotations

import argparse
import ast
import collections
import sys
from pathlib import Path

# 默认扫描的顶层目录（相对仓库根）
DEFAULT_DIRS = ('Bot', 'Core', 'con-datapack-1.11')
# 跳过的路径片段：第三方副本、缓存、模型目录
SKIP_PARTS = ('napcat', 'GPT_SoVITS', 'node_modules', '__pycache__', 'models',
              '.temp', 'egg-info', '.git', '.pytest_cache', 'build', 'dist')

# 第一方模块根名（不算第三方）
FIRST_PARTY = {
    'agent', 'character', 'common', 'devices', 'event', 'framework', 'manager',
    'memory', 'pipeline', 'services', 'tools', 'ui', 'asr', 'llm', 'mllm', 'tts',
    'ocr', 'img_cap', 'web_search', 'condata', 'con_datapack', 'utils', 'data',
    'config', 'starter', 'bot', 'main', 'abs_app', 'abs_model', 'decorator',
    'microphone', 'turnsense',
}

# 值得专门追版本的第三方包（★ 标记）
DEFAULT_WATCH = {
    'torch', 'torchaudio', 'torchvision', 'numpy', 'scipy', 'librosa', 'soundfile',
    'sounddevice', 'pyaudio', 'webrtcvad', 'pydub', 'pygame', 'onnxruntime',
    'sherpa_onnx', 'funasr', 'transformers', 'sentencepiece', 'kaldi_native_fbank',
    'openai', 'requests', 'httpx', 'websockets', 'pydantic', 'yaml', 'loguru',
    'PySide6', 'PyQt5', 'pynput', 'pyautogui', 'PIL', 'watchdog', 'magic',
    'typeguard', 'flask', 'paddle', 'paddleocr',
}

# 标准库（属性链统计里排除，避免噪声）
STDLIB = {
    'os', 'sys', 'json', 're', 'time', 'math', 'io', 'csv', 'pathlib', 'typing',
    'dataclasses', 'collections', 'threading', 'asyncio', 'subprocess', 'shutil',
    'glob', 'uuid', 'base64', 'hashlib', 'random', 'enum', 'abc', 'functools',
    'itertools', 'copy', 'traceback', 'socket', 'struct', 'wave', 'tempfile',
    'datetime', 'logging', 'contextlib', 'urllib', 'http', 'queue', 'inspect',
    'importlib', 'argparse', 'atexit', 'signal', 'string', 'textwrap', 'stat',
    'platform', 'html', 'types', 'ipaddress', 'statistics', 'ssl', 'zipfile',
    'ctypes', 'pkgutil', 'faulthandler', 'contextvars', 'concurrent', 'secrets',
}


def discover_files(root: Path, dirs) -> list[Path]:
    files = []
    for d in dirs:
        base = root / d
        if not base.exists():
            continue
        for p in base.rglob('*.py'):
            rel = str(p.relative_to(root))
            if any(s in rel for s in SKIP_PARTS):
                continue
            files.append(p)
    return sorted(files)


def scan(root: Path, dirs, watch: set[str]):
    files = discover_files(root, dirs)
    imports: collections.Counter = collections.Counter()
    from_imports: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    attr_chains: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    dynamic: list[tuple[str, int, str, str | None]] = []
    parse_errors: list[tuple[str, str]] = []

    for path in files:
        rel = str(path.relative_to(root))
        try:
            tree = ast.parse(path.read_text(encoding='utf-8', errors='replace'))
        except SyntaxError as exc:
            parse_errors.append((rel, str(exc)))
            continue

        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    root_name = a.name.split('.')[0]
                    aliases[a.asname or root_name] = a.name
                    if root_name not in FIRST_PARTY and root_name not in STDLIB:
                        imports[root_name] += 1
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                mod = node.module or ''
                root_name = mod.split('.')[0]
                third = root_name and root_name not in FIRST_PARTY and root_name not in STDLIB
                if third:
                    imports[root_name] += 1
                for a in node.names:
                    if third:
                        from_imports[root_name][a.name] += 1
                    aliases[a.asname or a.name] = f'{mod}.{a.name}' if mod else a.name

        def chain_of(node):
            parts = []
            cur = node
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
                parts.reverse()
                return parts
            return None

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                parts = chain_of(node)
                if not parts or len(parts) < 2:
                    continue
                head = parts[0]
                if head in FIRST_PARTY or head in STDLIB or head in ('self', 'cls'):
                    continue
                real = aliases.get(head, head)
                attr_chains[real]['.'.join(parts[1:])] += 1

            if isinstance(node, ast.Call):
                fname = None
                if isinstance(node.func, ast.Attribute):
                    fname = node.func.attr
                elif isinstance(node.func, ast.Name):
                    fname = node.func.id
                if fname in ('import_module', 'find_spec', '__import__'):
                    arg = node.args[0] if node.args else None
                    if isinstance(arg, ast.Constant):
                        lit = str(arg.value)
                    elif isinstance(arg, ast.JoinedStr):
                        lit = '<f-string>'
                    elif isinstance(arg, ast.Name):
                        lit = f'<var {arg.id}>'
                    else:
                        lit = None
                    dynamic.append((rel, node.lineno, fname, lit))

    return dict(files=files, imports=imports, from_imports=from_imports,
                attr_chains=attr_chains, dynamic=dynamic, parse_errors=parse_errors)


def render(result, watch: set[str]) -> tuple[str, dict]:
    out: list[str] = []

    def add(line: str = '') -> None:
        out.append(line)

    files = result['files']
    imports = result['imports']
    from_imports = result['from_imports']
    attr_chains = result['attr_chains']

    add('=' * 78)
    add(f'扫描文件数：{len(files)}（第一方 Python 文件，已跳过第三方副本与缓存）')
    add('=' * 78)
    add()

    add('一、被 import 的第三方包（按出现次数，★ = 值得追版本）')
    add('-' * 78)
    for mod, cnt in imports.most_common():
        add(f'  {mod:<24} {cnt:>4}{" ★" if mod in watch else ""}')
    add()

    add('二、值得追版本的包：实际用到的 API 面')
    add('-' * 78)
    visits = 0
    distinct_leaves = 0
    distinct_names = 0
    for mod in sorted(watch):
        chains = attr_chains.get(mod)
        names = from_imports.get(mod)
        if not chains and not names:
            continue
        add(f'### {mod}')
        if names:
            funcs = sorted(n for n in names if not n.isupper())
            consts = sorted(n for n in names if n.isupper())
            if funcs:
                add(f'  from-import 名字（{len(funcs)} 个不同名字）：')
                for n in funcs:
                    add(f'      {n}')
                distinct_names += len(funcs)
            if consts:
                add(f'  常量：{", ".join(consts)}')
        if chains:
            real = sorted((c for c in chains if c), key=lambda c: (-chains[c], c))
            add(f'  mod.attr 访问链（{len(real)} 个不同链）：')
            for c in real[:80]:
                add(f'      {mod}.{c}   ×{chains[c]}')
            if len(real) > 80:
                add(f'      … 其余 {len(real) - 80} 个省略')
            visits += sum(chains[c] for c in real)
            distinct_leaves += len(real)
        add()

    add('  口径说明：')
    add(f'    · 不同 API 名字（from-import）: {distinct_names}')
    add(f'    · 不同属性链（mod.attr 形式）: {distinct_leaves}')
    add(f'    · 属性链在代码里出现的次数合计: {visits}（同一个链出现多次会重复计数）')
    add(f'    · **需要人工核对的不同 API 总数** ≈ {distinct_names + distinct_leaves}')
    add('    · 注意：属性链包含前缀，例如 `torch.cuda` 与 `torch.cuda.is_bf16_supported`')
    add('      会分别计入，因此该数字是"偏保守的上界"而非精确去重结果。')
    add()

    add('三、动态 import（静态分析盲区，必须人工确认）')
    add('-' * 78)
    if result['dynamic']:
        for rel, ln, fn, lit in sorted(set(result['dynamic'])):
            add(f'  {rel}:{ln}  {fn}({lit})')
    else:
        add('  （无）')
    add()

    add('四、解析失败的文件')
    add('-' * 78)
    if result['parse_errors']:
        for rel, err in result['parse_errors']:
            add(f'  {rel}  {err}')
    else:
        add('  （无）')
    add()

    stats = {'files': len(files), 'distinct_apis': distinct_names + distinct_leaves,
             'distinct_names': distinct_names, 'distinct_chains': distinct_leaves,
             'chain_visits': visits,
             'dynamic': len(set(result['dynamic'])), 'parse_errors': len(result['parse_errors']),
             'third_party_roots': len(imports)}
    return '\n'.join(out), stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='静态扫描第三方 API 面')
    ap.add_argument('--root', default=None, help='仓库根目录（默认：本脚本上溯两级）')
    ap.add_argument('--dirs', default=','.join(DEFAULT_DIRS), help='要扫描的顶层目录，逗号分隔')
    ap.add_argument('--watch', default=None, help='要重点统计的包，逗号分隔（默认内置清单）')
    ap.add_argument('--out', default=None, help='把完整报告写到该文件（默认只打印摘要）')
    args = ap.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[2]
    dirs = [d.strip() for d in args.dirs.split(',') if d.strip()]
    watch = {w.strip() for w in args.watch.split(',')} if args.watch else set(DEFAULT_WATCH)

    result = scan(root, dirs, watch)
    report, stats = render(result, watch)
    print(report)

    if args.out:
        Path(args.out).write_text(report, encoding='utf-8')
        print(f'\n报告已写入：{Path(args.out).resolve()}', file=sys.stderr)

    print('--- 摘要 ---', file=sys.stderr)
    for k, v in stats.items():
        print(f'  {k}: {v}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
