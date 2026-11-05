# Character structure lookup data

`char_structure.json.gz` is a compact offline table used by
`stackchan_mcp.char_structure` so voice dictation hints can say a real
`structure` / `radical` (e.g. 湖 → 左右 + 三點水) instead of letting the LLM invent them.

## Contents

- `structures`: ordered labels (index is the digit stored in each entry)
- `radicals`: IDS component form → 香港廣東話 nickname
- `entries`: map of CJK char → `"<struct_index>[<radical_char>]"`  
  Example: `"湖": "0氵"` → structure `左右`, radical nickname for `氵`

Size target: gzipped JSON under ~100 KB (currently ~72 KB, ~27k BMP/Ext-A chars).

## Rebuild from cjkvi IDS

Source: [cjkvi-ids](https://github.com/cjkvi/cjkvi-ids) `ids.txt`
(CHISE-derived). Do **not** vendor the full ~2 MB IDS dump in git.

```bash
# from gateway/:
python3 tools/build_char_structure.py /path/to/ids.txt
```

Mapping of IDS operators:

| IDS | structure |
|-----|-----------|
| ⿰ | 左右 |
| ⿱ | 上下 |
| ⿲ | 左中右 |
| ⿳ | 上中下 |
| ⿴ | 全包圍 |
| ⿵⿶⿷⿸⿹⿺ | 半包圍 |
| ⿻ | 交疊 |
| single-component IDS | 單一 |

Radical: first IDS component when it is a known radical form in `radicals`
(e.g. 氵扌木火…). Nested IDS (operator as first component) stores structure only.

Optional override: set `STACKCHAN_CHAR_STRUCTURE_PATH` to another `.json` /
`.json.gz` file (same schema), or to a raw cjkvi `ids.txt` (loaded and derived
at runtime; slower first call).
