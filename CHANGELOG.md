# Changelog

この形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に沿い、
版の付け方は [Semantic Versioning](https://semver.org/lang/ja/) に従います。

## [0.1.0] - 2026-10-08

### Added

- 最初の版。vault を読むだけで点検する `vaultvet` コマンド。
- 検査 6 種: `broken_links`(行き先の無いリンク)、`broken_headings`(見出し・ブロック ID の無いリンク)、
  `orphans`(どこからもリンクされていないノート)、`dup_names`(同じ名前のノートが複数あって `[[名前]]` が曖昧)、
  `empty`(0 バイト・フロントマターだけのノート)、`unused_attachments`(使われていない添付ファイル)。
- リンクの解決は Obsidian に合わせた: `.md` の省略、パス付き/無し、最短パスの一致、大文字小文字の無視、
  フロントマターの `aliases`、URL エンコードされた Markdown リンク、`.canvas` からの参照。
- コードブロック(``` と ~~~)・インラインコード・`<code>` の中のリンクは数えない。
- ドットで始まるフォルダ、シンボリックリンク、ジャンクションは見ない。`--exclude <glob>` で除外を足せる。
- 出力はテキスト / `--json` / `--format markdown`。`--out` は vault の中を指すと拒否する。
- 終了コード: 0=検出なし、1=検出あり、2=引数や vault の不備。
- フォルダの一覧とファイルの読み込みをスレッドで並べ、WSL の `/mnt/...` 越しでも速く回る。
