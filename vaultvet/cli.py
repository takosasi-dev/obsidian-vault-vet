"""引数を読んで scan.run を呼び、結果をテキスト / JSON / Markdown で出す。

終了コード: 0=検出なし、1=検出あり、2=引数や vault の不備。
"""

import argparse
import json
import os
import sys

from . import __version__
from .scan import CHECKS, LABELS, run


def _where(f):
    return f"{f['file']}:{f['line']}" if f["line"] else f["file"]


def _summary(res):
    s = res["stats"]
    line = (f"ノート {s['notes']} 件 / 添付 {s['attachments']} 件 / ファイル全体 {s['files']} 件"
            f" / {s['seconds']:.2f} 秒")
    if s["excluded"]:
        line += f"(うち除外 {s['excluded']} 件)"
    return line


def render_text(res):
    out = [f"VaultVet {__version__}  {res['vault']}", _summary(res), ""]
    for name, items in res["checks"].items():
        out.append(f"■ {name}({LABELS[name]}): {len(items)} 件")
        for f in items:
            out.append(f"  {_where(f)}" + (f" → {f['detail']}" if f["detail"] else ""))
    if res["unreadable"]:
        out.append(f"■ 読めなかったファイル: {len(res['unreadable'])} 件")
        out += [f"  {r}" for r in res["unreadable"]]
    return "\n".join(out) + "\n"


def render_markdown(res):
    out = ["# VaultVet の結果", "", f"- vault: `{res['vault']}`", f"- {_summary(res)}", ""]
    for name, items in res["checks"].items():
        out += [f"## {name}({LABELS[name]}): {len(items)} 件", ""]
        for f in items:
            out.append(f"- `{_where(f)}`" + (f" → {f['detail']}" if f["detail"] else ""))
        if items:
            out.append("")
    if res["unreadable"]:
        out += [f"## 読めなかったファイル: {len(res['unreadable'])} 件", ""]
        out += [f"- `{r}`" for r in res["unreadable"]]
    return "\n".join(out).rstrip() + "\n"


def render_json(res):
    doc = {
        "tool": "vaultvet",
        "version": __version__,
        "vault": res["vault"],
        "stats": res["stats"],
        "unreadable": res["unreadable"],
        "checks": {n: {"count": len(i), "items": i} for n, i in res["checks"].items()},
    }
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def _inside(path, root):
    p = os.path.normcase(os.path.realpath(path))
    r = os.path.normcase(os.path.realpath(root))
    try:
        return os.path.commonpath([p, r]) == r
    except ValueError:  # Windows でドライブが違う
        return False


def build_parser():
    ap = argparse.ArgumentParser(
        prog="vaultvet",
        description="Obsidian の vault を読むだけで点検し、あとで困るものを一覧にする",
    )
    ap.add_argument("vault", help="vault のフォルダ")
    ap.add_argument("--only", help="走らせる検査をカンマ区切りで: " + ",".join(CHECKS))
    ap.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                    help="見ないパス(vault からの相対パスか名前に当てる glob)。何度でも")
    ap.add_argument("--format", choices=("text", "json", "markdown"), default="text")
    ap.add_argument("--json", action="store_const", const="json", dest="format",
                    help="--format json と同じ")
    ap.add_argument("--out", help="結果をこのファイルに書く(vault の中は不可)")
    ap.add_argument("--version", action="version", version=f"vaultvet {__version__}")
    return ap


def main(argv=None):
    try:  # 日本語を出せない端末やパイプで落ちないように
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    args = build_parser().parse_args(argv)

    def fail(msg):
        print(f"vaultvet: {msg}", file=sys.stderr)
        return 2

    if not os.path.isdir(args.vault):
        return fail(f"vault のフォルダがありません: {args.vault}")
    only = CHECKS
    if args.only:
        only = tuple(c.strip() for c in args.only.split(",") if c.strip())
        bad = [c for c in only if c not in CHECKS]
        if bad or not only:
            return fail(f"知らない検査: {','.join(bad)}(使えるのは {','.join(CHECKS)})")
    if args.out and _inside(args.out, args.vault):
        return fail(f"--out が vault の中を指しています。vault には書きません: {args.out}")

    res = run(args.vault, only, args.exclude)
    text = {"text": render_text, "json": render_json, "markdown": render_markdown}[args.format](res)
    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        except OSError as e:
            return fail(f"--out に書けません: {e}")
    else:
        sys.stdout.write(text)
    return 1 if any(res["checks"].values()) else 0
