"""vault を読んで点検する中身。引数と出力の形は cli.py が持つ。

vault の中には何も書かない。ファイルは1回ずつだけ読む。
"""

import json
import os
import posixpath
import re
import stat
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from fnmatch import fnmatch
from urllib.parse import unquote

CHECKS = (
    "broken_links",
    "broken_headings",
    "orphans",
    "dup_names",
    "empty",
    "unused_attachments",
)

LABELS = {
    "broken_links": "行き先の無いリンク",
    "broken_headings": "見出し・ブロックの無いリンク",
    "orphans": "どこからもリンクされていないノート",
    "dup_names": "名前だけでは行き先が決まらないリンク",
    "empty": "中身の無いノート",
    "unused_attachments": "使われていない添付ファイル",
}

# Obsidian が添付として開ける形式。unused_attachments はこれだけを見る
MEDIA_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp", ".avif",
    ".mp3", ".wav", ".m4a", ".ogg", ".flac", ".3gp", ".webm",
    ".mp4", ".ogv", ".mov", ".mkv", ".pdf",
}

WIKI_RE = re.compile(r"(!?)\[\[([^\[\]\n]+?)\]\]")
# [text](target "title")。text の中の [..] は1段だけ許す
MD_RE = re.compile(
    r"(!?)\[(?:[^\[\]\n]|\[[^\]\n]*\])*\]\(\s*(<[^>\n]+>|[^)\s]+)"
    r"(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)"
)
SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
# インラインコードは空白でない印に置き換える。消すと `(sd `x`)` が (sd ) になってリンクに化ける
INLINE_CODE_RE = re.compile(r"(`+)(.+?)\1|<code>.*?</code>")
CODE_MARK = "\x00"  # \x1c-\x1f は正規表現の \s に入るので使わない
HEADING_RE = re.compile(r"^#{1,6}[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
BLOCK_RE = re.compile(r"(?:^|\s)\^([A-Za-z0-9-]+)\s*$")
ALIAS_KEY_RE = re.compile(r"^(?:aliases|alias)\s*:\s*(.*)$")
HEADING_JUNK_RE = re.compile(r"[#^\[\]|*:%`\\]+")


def key(s: str) -> str:
    """名前の突き合わせ用。Obsidian は大文字小文字を区別しない。NFD の名前もそろえる"""
    return unicodedata.normalize("NFC", s).lower()


def heading_key(s: str) -> str:
    """見出しの突き合わせ用。リンクに書けない記号は空白とみなして詰める"""
    return " ".join(HEADING_JUNK_RE.sub(" ", key(s)).split())


def github_slug(s: str) -> str:
    """GitHub 向けに書かれた README の [x](#見出しの-slug) も通す"""
    return re.sub(r"[^\w\- ]", "", key(s)).replace(" ", "-")


@dataclass
class Link:
    line: int
    raw: str
    path: str  # 空ならそのノート自身
    sub: str  # '#' の後ろ。見出しか ^ブロックID
    md: bool  # [text](path) の形


@dataclass
class Note:
    rel: str
    links: list = field(default_factory=list)
    headings: set = field(default_factory=set)
    blocks: set = field(default_factory=set)
    aliases: list = field(default_factory=list)
    empty: str = ""  # 空なら理由。中身があれば ""


def _parse_aliases(lines):
    out = []
    for i, line in enumerate(lines):
        m = ALIAS_KEY_RE.match(line)
        if not m:
            continue
        v = m.group(1).strip()
        if v.startswith("["):
            out += v.strip("[]").split(",")
        elif v:
            out.append(v)
        else:
            for item in lines[i + 1:]:
                s = item.strip()
                if not s.startswith("- "):
                    break
                out.append(s[2:])
    return [a.strip().strip("\"'") for a in out if a.strip().strip("\"'")]


def _links_in(line, lineno, out):
    if "[" not in line:
        return
    for m in WIKI_RE.finditer(line):
        target = m.group(2).split("|", 1)[0]
        if target.endswith("\\"):  # 表の中の [[note\|表示名]]
            target = target[:-1]
        path, _, sub = target.partition("#")
        if path.strip() or sub.strip():
            out.append(Link(lineno, m.group(0), path.strip(), sub.strip(), False))
    rest = WIKI_RE.sub(" ", line)
    if "](" not in rest:
        return
    for m in MD_RE.finditer(rest):
        url = m.group(2)
        if url.startswith("<"):
            url = url[1:-1]
        if SCHEME_RE.match(url):  # http: mailto: obsidian: など
            continue
        path, _, sub = unquote(url).partition("#")
        if path.strip() or sub.strip():
            raw = m.group(0).replace(CODE_MARK, "`…`")
            out.append(Link(lineno, raw, path.strip(), sub.strip(), True))


def parse_note(rel: str, text: str) -> Note:
    note = Note(rel)
    lines = text.splitlines()
    body_start = 0
    if lines and lines[0].rstrip() == "---":
        for i in range(1, len(lines)):
            if lines[i].rstrip() in ("---", "..."):
                body_start = i + 1
                note.aliases = _parse_aliases(lines[1:i])
                break
    fence = ""
    has_body = False
    for i, line in enumerate(lines):
        lineno = i + 1
        in_body = i >= body_start
        if in_body and line.strip():
            has_body = True
        if fence:
            s = line.strip()
            if s.startswith(fence) and not s.strip(fence[0]):
                fence = ""
            continue
        if in_body:
            m = FENCE_RE.match(line)
            if m:
                fence = m.group(1)
                continue
            if line.startswith("#"):
                h = HEADING_RE.match(line)
                if h:
                    note.headings.add(heading_key(h.group(1)))
                    note.headings.add(github_slug(h.group(1)))
            if "^" in line:
                b = BLOCK_RE.search(line)
                if b:
                    note.blocks.add(b.group(1).lower())
        if "`" in line or "<code>" in line:
            line = INLINE_CODE_RE.sub(CODE_MARK, line)
        # フロントマターの "[[note]]" も Obsidian はリンクとして数える
        _links_in(line, lineno, note.links)
    if not text:
        note.empty = "0 バイト"
    elif not has_body:
        note.empty = "フロントマターだけ" if body_start else "空白だけ"
    return note


def canvas_refs(text: str):
    """.canvas から参照を拾う。壊れていても落ちず、拾えた分だけ返す"""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out = []
    for node in data.get("nodes", []) if isinstance(data, dict) else []:
        if not isinstance(node, dict):
            continue
        if node.get("type") == "file" and isinstance(node.get("file"), str):
            out.append(Link(0, node["file"], node["file"], "", False))
        elif node.get("type") == "text" and isinstance(node.get("text"), str):
            for line in node["text"].splitlines():
                _links_in(line, 0, out)
    return out


def _is_link(entry) -> bool:
    """シンボリックリンクとジャンクションは辿らない"""
    if entry.is_symlink():
        return True
    if hasattr(entry, "is_junction"):  # 3.12 以降
        return entry.is_junction()
    if os.name == "nt" and entry.is_dir(follow_symlinks=False):
        try:
            attrs = entry.stat(follow_symlinks=False).st_file_attributes
        except OSError:
            return True
        return bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    return False


def _excluded(rel, name, patterns):
    return any(fnmatch(rel, p) or fnmatch(name, p) for p in patterns)


# WSL の /mnt/e のような遅いファイルシステムでは1回ごとの待ちが長いので、
# フォルダの一覧とファイルの読み込みはスレッドで並べて待つ
WORKERS = 16


def _list_dir(job, patterns):
    rel_dir, abs_dir, ex = job
    dirs, files = [], []
    try:
        it = os.scandir(abs_dir)
    except OSError:
        return dirs, files
    with it:
        for e in it:
            if e.name.startswith(".") or _is_link(e):
                continue
            rel = f"{rel_dir}/{e.name}" if rel_dir else e.name
            exd = ex or _excluded(rel, e.name, patterns)
            if e.is_dir(follow_symlinks=False):
                dirs.append((rel, e.path, exd))
            elif e.is_file(follow_symlinks=False):
                files.append((rel, e.path, exd))
    return dirs, files


def walk(root, exclude=(), pool=None):
    """(vault からの相対パス, 実パス, 除外か) を返す。ドットで始まる物は見ない"""
    patterns = [p.strip("/") for p in exclude if p.strip("/")]
    if pool is None:
        with ThreadPoolExecutor(WORKERS) as own:
            return walk(root, exclude, own)
    files = []
    level = [("", root, False)]
    while level:
        nxt = []
        for dirs, fs in pool.map(lambda j: _list_dir(j, patterns), level):
            nxt += dirs
            files += fs
        level = nxt
    files.sort()
    return files


def _load(rel, path):
    """1ファイルを1回だけ読んで解析する。読めなければ None"""
    try:
        with open(path, "rb") as f:
            text = f.read().decode("utf-8", "replace").lstrip("﻿")
    except OSError:
        return None
    return parse_note(rel, text) if rel.lower().endswith(".md") else canvas_refs(text)


class Index:
    """Obsidian と同じ順でリンクの行き先を探す"""

    def __init__(self, rels, notes):
        self.by_path = {key(r): r for r in rels}
        self.by_name = {}
        for r in rels:
            self.by_name.setdefault(key(posixpath.basename(r)), []).append(r)
        self.aliases = {}
        for n in notes.values():
            for a in n.aliases:
                self.aliases.setdefault(key(a), n.rel)

    def resolve(self, path, src):
        """(行き先 or None, 名前で一致した候補の一覧) を返す"""
        if not path:
            return src, [src]
        p = unicodedata.normalize("NFC", path).replace("\\", "/")
        src_dir = posixpath.dirname(src)
        tries = [p] if p.lower().endswith(".md") else [p + ".md", p]
        for cand in tries:
            if cand.startswith("/"):
                hit = self.by_path.get(key(posixpath.normpath(cand.lstrip("/"))))
                if hit:
                    return hit, [hit]
                continue
            rel = posixpath.normpath(posixpath.join(src_dir, cand))
            hit = self.by_path.get(key(rel)) or self.by_path.get(key(posixpath.normpath(cand)))
            if hit:
                return hit, [hit]
            k = key(cand)
            matches = [
                r for r in self.by_name.get(key(posixpath.basename(cand)), [])
                if key(r) == k or key(r).endswith("/" + k)
            ]
            if matches:
                same = [r for r in matches if posixpath.dirname(r) == src_dir]
                best = same[0] if same else min(matches, key=lambda r: (len(r), r))
                return best, matches
        if "/" not in p:
            hit = self.aliases.get(key(p))
            if hit:
                return hit, [hit]
        return None, []


def _ext(rel):
    return posixpath.splitext(rel)[1].lower()


def run(root, only=CHECKS, exclude=()):
    """vault を点検して結果を dict で返す。root が vault の根"""
    t0 = time.perf_counter()
    with ThreadPoolExecutor(WORKERS) as pool:
        files = walk(root, exclude, pool)
        todo = [(r, p) for r, p, ex in files if not ex and _ext(r) in (".md", ".canvas")]
        loaded = pool.map(lambda rp: _load(*rp), todo)
        rels = [r for r, _, _ in files]
        notes = {}
        canvases = {}
        unreadable = []
        for (rel, _), got in zip(todo, loaded):
            if got is None:
                unreadable.append(rel)
            elif isinstance(got, Note):
                notes[rel] = got
            else:
                canvases[rel] = got

    index = Index(rels, notes)
    found = {c: [] for c in only}
    incoming = set()  # 他のファイルから参照されている物

    def add(check, rel, line, detail=""):
        if check in found:
            found[check].append({"file": rel, "line": line, "detail": detail})

    for rel, links in list(notes.items()) + list(canvases.items()):
        is_note = rel in notes
        links = links.links if is_note else links
        for ln in links:
            target, matches = index.resolve(ln.path, rel)
            if target is None:
                if is_note:
                    add("broken_links", rel, ln.line, ln.raw)
                continue
            if target != rel:
                incoming.add(target)
            if not is_note:
                continue
            if len(matches) > 1 and "/" not in ln.path and _ext(target) == ".md":
                add("dup_names", rel, ln.line,
                    f"{ln.raw} が {len(matches)} 件に一致: " + ", ".join(sorted(matches)))
            tnote = notes.get(target)
            if ln.sub and tnote is not None:
                if ln.sub.startswith("^"):
                    if ln.sub[1:].lower() not in tnote.blocks:
                        add("broken_headings", rel, ln.line,
                            f"{ln.raw}: ブロック「{ln.sub}」が {target} に無い")
                else:
                    last = ln.sub.split("#")[-1]
                    if (heading_key(last) not in tnote.headings
                            and github_slug(last) not in tnote.headings):
                        add("broken_headings", rel, ln.line,
                            f"{ln.raw}: 見出し「{last}」が {target} に無い")

    for rel, note in notes.items():
        if rel not in incoming:
            add("orphans", rel, None)
        if note.empty:
            add("empty", rel, None, note.empty)
    for rel, _, ex in files:
        if not ex and _ext(rel) in MEDIA_EXT and rel not in incoming:
            add("unused_attachments", rel, None)

    for items in found.values():
        items.sort(key=lambda f: (f["file"], f["line"] or 0))
    return {
        "vault": str(root),
        "stats": {
            "notes": sum(1 for r in rels if _ext(r) == ".md"),
            "attachments": sum(1 for r in rels if _ext(r) in MEDIA_EXT),
            "files": len(rels),
            "excluded": sum(1 for _, _, ex in files if ex),
            "seconds": round(time.perf_counter() - t0, 3),
        },
        "unreadable": unreadable,
        "checks": found,
    }
