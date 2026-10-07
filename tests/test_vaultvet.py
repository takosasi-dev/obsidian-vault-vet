"""tests/fixtures/vault(手で作った架空の vault)で各検査の当たり・外れを見る。"""

import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import unicodedata
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vaultvet.cli import main  # noqa: E402
from vaultvet.scan import parse_note, run  # noqa: E402

VAULT = ROOT / "tests" / "fixtures" / "vault"


def pairs(res, check):
    return {(f["file"], f["line"]) for f in res["checks"][check]}


def snapshot(root):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for n in filenames:
            p = os.path.join(dirpath, n)
            st = os.stat(p)
            out[p] = (st.st_size, st.st_mtime_ns)
        out[dirpath] = len(dirnames) + len(filenames)
    return out


def cli(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main([str(a) for a in args])
        except SystemExit as e:  # argparse の不備
            code = e.code
    return code, out.getvalue(), err.getvalue()


class FixtureChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = run(str(VAULT))

    def test_broken_links(self):
        self.assertEqual(pairs(self.res, "broken_links"), {
            ("Home.md", 27), ("Home.md", 28), ("Home.md", 29),
            ("Templates/Template.md", 3),
        })

    def test_code_and_dot_folders_are_ignored(self):
        details = " ".join(f["detail"] for c in self.res["checks"].values() for f in c)
        for word in ("In Code Fence", "In Tilde Fence", "inline code", "In Html Code", "sd ",
                     "Broken In Dot Folder", "Broken In Trash", "example.com"):
            self.assertNotIn(word, details)

    def test_broken_headings(self):
        self.assertEqual(pairs(self.res, "broken_headings"),
                         {("Home.md", 30), ("Home.md", 31), ("Home.md", 32)})

    def test_orphans(self):
        # Aliased は別名、Canvased はキャンバス、b/Meeting はパス付きリンクで救われる
        self.assertEqual({f for f, _ in pairs(self.res, "orphans")},
                         {"Empty.md", "FrontOnly.md", "Orphan.md", "Templates/Template.md"})

    def test_dup_names(self):
        items = self.res["checks"]["dup_names"]
        self.assertEqual([(f["file"], f["line"]) for f in items], [("Home.md", 33)])
        self.assertIn("a/Meeting.md, b/Meeting.md", items[0]["detail"])

    def test_empty(self):
        self.assertEqual({(f["file"], f["detail"]) for f in self.res["checks"]["empty"]},
                         {("Empty.md", "0 バイト"), ("FrontOnly.md", "フロントマターだけ")})

    def test_unused_attachments(self):
        self.assertEqual({f for f, _ in pairs(self.res, "unused_attachments")},
                         {"unused.png", "attachments/old photo.jpg"})

    def test_stats(self):
        self.assertEqual(self.res["stats"]["notes"], 10)
        self.assertEqual(self.res["stats"]["attachments"], 5)


class ParseNote(unittest.TestCase):
    def test_parts(self):
        n = parse_note("x.md", "---\naliases:\n  - 'A'\n  - B\nup: \"[[Parent]]\"\n---\n"
                       "## Heading `code` #\ntext ^Blk-1\n````\n```\n[[no]]\n````\n[[yes#h|x]]\n")
        self.assertEqual(n.aliases, ["A", "B"])
        self.assertEqual(n.headings, {"heading code", "heading-code"})
        self.assertEqual(n.blocks, {"blk-1"})
        self.assertEqual([(ln.path, ln.sub) for ln in n.links], [("Parent", ""), ("yes", "h")])
        self.assertEqual(n.empty, "")

    def test_empty_kinds(self):
        self.assertEqual(parse_note("e.md", "").empty, "0 バイト")
        self.assertEqual(parse_note("e.md", " \n\n").empty, "空白だけ")
        self.assertEqual(parse_note("e.md", "---\na: 1\n---\n").empty, "フロントマターだけ")
        self.assertEqual(parse_note("e.md", "---\nunclosed\n").empty, "")


class Cli(unittest.TestCase):
    def test_exit_codes_and_only(self):
        self.assertEqual(cli(VAULT)[0], 1)
        code, out, _ = cli(VAULT, "--only", "broken_links,orphans", "--json")
        self.assertEqual(code, 1)
        doc = json.loads(out)
        self.assertEqual(list(doc["checks"]), ["broken_links", "orphans"])
        self.assertEqual(doc["checks"]["broken_links"]["count"], 4)

    def test_bad_input_is_2(self):
        self.assertEqual(cli(VAULT / "no-such-dir")[0], 2)
        self.assertEqual(cli(VAULT, "--only", "nope")[0], 2)
        self.assertEqual(cli(VAULT, "--bogus")[0], 2)
        code, _, err = cli(VAULT, "--out", VAULT / "sub" / "report.txt")
        self.assertEqual(code, 2)
        self.assertIn("vault", err)
        self.assertFalse((VAULT / "sub").exists())

    def test_exclude(self):
        code, out, _ = cli(VAULT, "--exclude", "Templates", "--json")
        doc = json.loads(out)
        files = {f["file"] for c in doc["checks"].values() for f in c["items"]}
        self.assertFalse(any(f.startswith("Templates/") for f in files))
        self.assertEqual(doc["stats"]["excluded"], 1)

    def test_no_findings_is_0(self):
        code, out, _ = cli(VAULT, "--only", "dup_names", "--exclude", "Home.md")
        self.assertEqual(code, 0, out)

    def test_markdown_and_out_and_vault_untouched(self):
        before = snapshot(VAULT)
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "r.md"
            code, out, _ = cli(VAULT, "--format", "markdown", "--out", dest)
            self.assertEqual((code, out), (1, ""))
            text = dest.read_text(encoding="utf-8")
            self.assertIn("## broken_links", text)
            self.assertIn("- `Home.md:27` → [[Missing Note]]", text)
        self.assertEqual(snapshot(VAULT), before)


class TempVault(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, rel, text):
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def test_symlinks_are_not_followed(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside, True)
        (outside / "Outside.md").write_text("[[Gone]]", encoding="utf-8")
        self.write("Home.md", "x")
        try:
            os.symlink(outside, self.dir / "linked", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("シンボリックリンクを作れない環境")
        res = run(str(self.dir))
        self.assertEqual(res["stats"]["files"], 1)

    def test_nfd_names_and_url_encoding(self):
        nfd = unicodedata.normalize("NFD", "プラン")
        self.write(f"{nfd}.md", "x")
        self.write("dir/My Note.md", "x")
        self.write("Home.md", "[[プラン]] [a](dir/My%20Note.md) [[my note]]")
        res = run(str(self.dir), ("broken_links",))
        self.assertEqual(res["checks"]["broken_links"], [])

    def test_shortest_path_and_same_folder(self):
        self.write("x/deep/N.md", "deep")
        self.write("N.md", "top")
        self.write("x/deep/Src.md", "[[N]]")
        self.write("Top.md", "[[N]] [[Src]]")
        res = run(str(self.dir), ("orphans",))
        # 同じフォルダの x/deep/N.md と、最短の N.md の両方に行き先がある
        self.assertEqual({f["file"] for f in res["checks"]["orphans"]}, {"Top.md"})

    def test_speed_thousands_of_notes(self):
        for i in range(3000):
            self.write(f"d{i % 30}/n{i}.md",
                       f"# n{i}\n\n[[n{(i + 1) % 3000}]] [[n{i}#n{i}]] ![[img{i % 50}.png]]\n" * 5)
        run(str(self.dir))  # 書いた直後の初回は Windows のウイルス検査で遅いので、2回目を測る
        t = time.perf_counter()
        res = run(str(self.dir))
        took = time.perf_counter() - t
        self.assertEqual(res["stats"]["notes"], 3000)
        self.assertEqual(res["checks"]["broken_headings"], [])
        self.assertLess(took, 5.0)


if __name__ == "__main__":
    unittest.main()
