"""导出各 conda 环境的带版本快照（environment.yml 风格）。

为什么需要它：Core 的几个服务跑在不同 conda 环境里，torch / funasr / sherpa-onnx 这些
是手工调过的 GPU 版本，靠 pip 解析装不出同一套。本脚本把每个环境的**实际版本**固化成
可重建的快照，作为"换机重建 + GPU 版本回滚"的基线。

用法（在仓库根目录）：
    python generate_env_snapshots.py                     # 导出默认 4 个环境到 ./环境快照/
    python generate_env_snapshots.py --out Core/envs     # 指定输出目录（需该目录可创建）
    python generate_env_snapshots.py -n MyAI -n tts      # 只导出指定环境

产物：<out>/environment-<env>.yml，每份头部带用途说明。
注意：脚本会剥掉 `prefix:` 绝对路径与 `pip:` 段（pip 包由 requirements.txt 负责），
保留 channels 与完整 build 字符串，所以是"带版本"的快照。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# 环境 -> 用途说明（与 Core/agent-stack/registry.json 保持一致）
ENV_PURPOSE = {
    "MyAI": "Bot 主程序（cd Bot && python main.py）",
    "qwen3-asr": "Core ASR 服务（127.0.0.1:11001）",
    "llm_deepseek_online": "Core LLM / MLLM / llm-memory / web_search（11000 / 10010 / 11012）",
    "GPTSoVits": "Core TTS 服务（127.0.0.1:11002，工作目录 Core/GPT-SoVITS）",
    "asr_paraformer": "可选：旧版 paraformer ASR 环境",
    "qwen-vlm": "可选：本地 VLM 环境",
}

DEFAULT_ENVS = ("MyAI", "qwen3-asr", "llm_deepseek_online", "GPTSoVits")


def _find_conda() -> str:
    """定位 conda.exe；找不到就退回 PATH 上的 conda。"""
    candidates = [
        Path.home() / "miniconda3" / "Scripts" / "conda.exe",
        Path.home() / "anaconda3" / "Scripts" / "conda.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("conda")
    if found:
        return found
    raise SystemExit("找不到 conda.exe，请用 --conda 指定完整路径。")


def _sanitize(raw: str, env_name: str) -> tuple[str, dict[str, int]]:
    """剥掉 prefix 与 pip 段，插入用途说明；返回 (内容, 包数统计)。"""
    lines = raw.splitlines()
    kept: list[str] = []
    pip_count = 0
    in_pip = False
    conda_count = 0
    skipping_prefix = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("prefix:"):
            skipping_prefix = True
            continue
        if skipping_prefix:
            # prefix 之后不应再有内容，出现则说明格式变了，保守放弃后续行
            continue
        # conda 在 4 空格缩进下把 pip 依赖写成 "- pip:"；条目标记为 6 空格
        if stripped in {"pip:", "- pip:"}:
            in_pip = True
            continue
        if in_pip:
            if stripped.startswith("- "):
                pip_count += 1
                continue
            # pip 段结束（缩进回到 dependencies 层级）
            in_pip = False
        if line.startswith("  - ") and not stripped.startswith("- pip:"):
            conda_count += 1
        kept.append(line)

    purpose = ENV_PURPOSE.get(env_name, "（用途未登记）")
    header = [
        f"# conda 环境快照：{env_name}",
        f"# 用途：{purpose}",
        f"# 由 generate_env_snapshots.py 从实际环境导出；channels 与 build 字符串已保留。",
        f"# 已剔除 {pip_count} 个 pip 包条目（pip 依赖以 Bot/requirements.txt 与 Core/requirements-<环境名>.txt 为准）。",
        "# 重建：conda env create -f <本文件>  （重建后仍需 pip install -e con-datapack-1.11）",
        "",
    ]
    return "\n".join(header + kept) + "\n", {"conda": conda_count, "pip": pip_count}


def _env_path(env_name: str) -> Path | None:
    """按名字猜环境路径。

    注意：不能依赖 `conda env export -n <名字>`——在 conda 25.x 上 `conda env list`
    可能因内部报错而返回空列表，此时 `-n` 会静默导出 base 环境（导出结果看着正常，
    内容却是错的）。所以这里直接用路径定位，并在导出前校验路径存在。
    """
    roots = [Path.home() / "miniconda3" / "envs", Path.home() / "anaconda3" / "envs"]
    for root in roots:
        candidate = root / env_name
        if candidate.is_dir():
            return candidate
    return None


def export_env(conda: str, env_name: str, out_dir: Path, env_path: Path) -> dict:
    result = subprocess.run(
        [conda, "env", "export", "-p", str(env_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0 or "name:" not in (result.stdout or ""):
        return {"env": env_name, "ok": False, "error": (result.stderr or "").strip()[:200] or "导出失败"}

    content, package_counts = _sanitize(result.stdout, env_name)
    target = out_dir / f"environment-{env_name}.yml"
    try:
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return {"env": env_name, "ok": False, "error": f"写入失败：{exc}"}
    return {"env": env_name, "ok": True, "path": str(target),
            "conda_packages": package_counts["conda"],
            "pip_packages": package_counts["pip"],
            "bytes": len(content.encode("utf-8"))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出 conda 环境快照（带版本）")
    parser.add_argument("--out", default="环境快照", help="输出目录（默认 ./环境快照）")
    parser.add_argument("-n", "--env", action="append", default=None,
                        help="要导出的环境名，可重复；默认导出 4 个主要环境")
    parser.add_argument("--conda", default=None, help="conda.exe 完整路径")
    args = parser.parse_args(argv)

    conda = args.conda or _find_conda()
    envs = args.env or list(DEFAULT_ENVS)
    out_dir = Path(args.out)

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"无法创建输出目录 {out_dir}：{exc}", file=sys.stderr)
        return 2

    ok = 0
    for env_name in envs:
        env_path = _env_path(env_name)
        if env_path is None:
            print(f"❌ {env_name:<22} 找不到环境目录（已跳过，避免导出成 base）", file=sys.stderr)
            continue
        info = export_env(conda, env_name, out_dir, env_path)
        if info["ok"]:
            ok += 1
            print(f"✅ {info['env']:<22} conda 包 {info['conda_packages']:>3}｜"
                  f"剔除 pip 包 {info['pip_packages']:>3} → {info['path']}")
        else:
            print(f"❌ {info['env']:<22} {info['error']}", file=sys.stderr)

    print(f"\n完成：{ok}/{len(envs)} 个环境已写入 {out_dir}")
    return 0 if ok == len(envs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
