---
aliases: [ホーム, Start]
---
# Home

## 当たり(行き先あり)

- [[Project A]]
- [[projects/project a|大文字違いのパス]]
- [[Project A#Goals]]
- [[Project A#^blk1]]
- ![[diagram.png]]
- [[report.pdf#page=2]]
- [md リンク](projects/Project%20A.md)
- [山かっこ](<projects/Project A.md#Goals>)
- [[別名ノート]]
- [[#Home]]
- [web](https://example.com/x.md)
- [メール](mailto:someone@example.com)

| 表 | リンク |
| -- | -- |
| 1 | [[Project A\|PA]] |

## 外れ(行き先なし)

- [[Missing Note]]
- ![[missing.png]]
- [壊れた md リンク](nowhere.md)
- [[Project A#No Such Heading]]
- [[Project A#^nope]]
- [[#Nope]]
- [[Meeting]]

## コードの中は数えない

```
[[In Code Fence]]
```

~~~python
x = "[[In Tilde Fence]]"
~~~

`[[inline code]]` もリンクではない
<code>![[In Html Code]]</code> も数えない
[消すとリンクに化ける](sd `print_report`) は数えない

## GitHub 形式の見出しリンクも通す

[slug](projects/Project%20A.md#実装メモ-案)
