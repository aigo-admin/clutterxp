# 02: Rule Engine

The rule engine turns one `FileInfo` into one `Decision`. It is **pure**: no file reads, no writes, no network, no clock except `mtime`/`now` passed in. Everything is compiled once when the config loads.

## 1. Rule fields

```yaml
- name: Invoices              # required, unique (case-insensitive)
  enabled: true               # default true
  priority: 10                # integer, LOWER WINS, default 100
  destination: ~/Documents/Invoices/{year}   # template, required
  keywords: [invoice, inv, bill]             # any-of; see matching below
  all_of: []                                 # every entry must match
  exclude: [draft]                           # if any matches, the rule does not apply
  match: token                               # token | prefix | substring (default for plain keywords)
  extensions: [pdf, docx]                    # optional, case-insensitive, may be compound "tar.gz"
  conditions:                                # optional, all must hold; cheap stat-based only
    min_size: 10KB
    max_size: 50MB
    older_than: 2d                           # mtime age
    newer_than: 30d
  collision: skip                            # skip | rename (overrides global default)
  create_destinations: leaf                  # never | leaf | all (overrides global default)
```

A rule must have at least one of `keywords`, `all_of`, `extensions`, or `conditions`. A rule with **no positive criterion** (only `exclude`) is a config error: it would match everything.

A rule matches a file when **all** of the following are true:

1. `enabled`
2. `extensions` empty OR file name ends with `.` + one of the entries (case-insensitive)
3. `keywords` empty OR at least one keyword matches
4. every `all_of` entry matches
5. no `exclude` entry matches
6. every `conditions` entry holds

## 2. Normalization

1. File name: `unicodedata.normalize("NFC", name)`.
2. Comparison text is `casefold()`ed (so `ß`, `İ`, etc. compare sanely). Matching is case-insensitive unless `settings.case_sensitive: true`.
3. **Stem** = name minus the final suffix if it looks like an extension (1 to 8 characters, `[A-Za-z0-9_+-]`, name has a dot that is not the first character). If the remaining stem ends with `.tar`, strip that too, so `x.tar.gz` has stem `x`.
4. Hidden files (leading dot) are `IGNORED` when `settings.skip_hidden` is true (default). Directories, symlinks (unless `follow_symlinks`), and non-regular files are `IGNORED`.

## 3. Tokenization

Applied to the **stem**, producing two token streams.

**Raw stream** (separators only):
- Split on every run of characters matching `[\W_]+` (anything that is not a Unicode letter/digit; the underscore counts as a separator).
- Casefold, drop empty tokens.

**Camel stream** (word boundaries inside each raw token, computed on the *original-case* token):
- Split between a lower-case letter and an upper-case letter: `SecLeaf` becomes `Sec`, `Leaf`.
- Split between an upper-case run and a following Capital+lower: `HTMLParser` becomes `HTML`, `Parser`.
- **Do not** split between letters and digits (`sl2` stays `sl2`, `v2` stays `v2`).
- Casefold the pieces.

A **keyword** is tokenized with the *raw* algorithm only (it is the user's literal text), so `sl-ctf` becomes `[sl, ctf]` and `sec leaf` becomes `[sec, leaf]`.

## 4. Match modes

| Mode | Rule | Applied to |
|---|---|---|
| `token` (default) | keyword's token sequence appears as a **contiguous run** in the raw stream **or** in the camel stream (not mixed) | stem tokens |
| `prefix` | keyword (single token) is a prefix of any raw token | stem tokens |
| `substring` | casefolded keyword occurs in casefolded stem | stem |
| `glob:` prefix | `fnmatch.fnmatchcase` on casefolded keyword vs casefolded **full name** | full name |
| `regex:` prefix | `re.search`, `re.IGNORECASE` unless case-sensitive, on **full name** | full name |

- Plain keywords use the rule's `match` value (default `token`). A keyword that starts with `glob:` or `regex:` always uses that mode.
- `substring` is explicit opt-in and the config validator warns when a substring keyword is shorter than 4 characters.
- Regexes are compiled at load time; invalid regexes are config errors. Catastrophic-backtracking protection: reject patterns with nested unbounded quantifiers like `(a+)+` using a simple static check, and always apply `re.search` only to a name (max 255 chars).

## 5. Test vectors (normative)

Default settings, `match: token`, case-insensitive. "✓" = keyword matches, "✗" = no match.

| File name | Keyword | Result | Reason |
|---|---|---|---|
| `SecLeaf_notes.pdf` | `secleaf` | ✓ | raw tokens `[secleaf, notes]` |
| `SecLeaf_notes.pdf` | `sl` | ✗ | no token equals `sl` |
| `SL-CTF-writeup.pdf` | `sl` | ✓ | raw `[sl, ctf, writeup]` |
| `SL-CTF-writeup.pdf` | `sl-ctf` | ✓ | contiguous `[sl, ctf]` |
| `SL-CTF-writeup.pdf` | `ctf-sl` | ✗ | wrong order |
| `slides.pdf` | `sl` | ✗ | token is `slides` |
| `tcpdump_capture.pcap` | `tc` | ✗ | token is `tcpdump` |
| `TC_report (1).docx` | `tc` | ✓ | raw `[tc, report, 1]` |
| `report-tc-final.docx` | `tc` | ✓ | token in the middle |
| `SecLeafNotes.pdf` | `secleaf` | ✗ | raw `[secleafnotes]`, camel `[sec, leaf, notes]` |
| `SecLeafNotes.pdf` | `sec leaf` | ✓ | camel stream contiguous run |
| `SecLeafNotes.pdf` | `secleaf` with `match: prefix` | ✓ | raw token starts with it |
| `HTMLParser.py` | `html` | ✓ | camel `[html, parser]` |
| `sl2_notes.txt` | `sl` | ✗ | digits not split: `sl2` |
| `invoice.2024.03.pdf` | `invoice` | ✓ | dots separate tokens |
| `backup.TAR.GZ` | ext filter `tar.gz` | ✓ | compound, case-insensitive |
| `archive.tar.gz` | stem | `archive` | `.tar` stripped as well |
| `.bashrc` | anything | IGNORED | hidden |
| `résumé_final.pdf` (NFD form) | `résumé` (NFC) | ✓ | NFC normalization |
| `x-ctf-y.pdf` | `glob:*-ctf-*` | ✓ | fnmatch on full name |
| `Report_2024.pdf` | `regex:^report_\d{4}` | ✓ | `re.IGNORECASE` |
| `draft_invoice.pdf` | rule keywords `invoice`, exclude `draft` | rule does not match | exclude wins |

Agents must turn this table into a parametrized test (`tests/rules/test_vectors.py`) and extend it with property tests: tokenization is idempotent, never raises, never produces empty tokens, and `decide` is deterministic.

## 6. Priority and ambiguity

1. Collect every enabled rule that matches (after per-folder rule subset filtering).
2. Zero matches: `NO_MATCH`.
3. Find the **lowest `priority` number** among the matches. These are the *top matches*.
4. One top match: `MATCH`.
5. Several top matches:
   - If all of them resolve to the **same destination directory** (after template expansion), the result is `MATCH` with the first one in file order, and the others are listed in `hit.also_matched`. The outcome is identical either way, so this is not ambiguous.
   - Otherwise: `AMBIGUOUS`, with every top match in `candidates`. **Nothing is moved.** The user resolves it by changing priorities, tightening keywords, or using `exclude`.
6. Lower-priority matches never cause ambiguity; they are listed in `also_matched` for `rules test` output.

`priority` defaults to `100`, so rules without explicit priorities that overlap are *ambiguous* (safe) rather than silently ordered by file position.

## 7. Destination templates

`destination` is a path template, expanded at decision time:

| Placeholder | Value |
|---|---|
| `~`, `$VAR`, `${VAR}` | expanded first; an unset variable is a config error |
| `{year}` `{month}` `{day}` | from the file's `mtime`, local time, zero-padded (`2026`, `11`, `06`) |
| `{ext}` | final extension, lowercase; `noext` if none |
| `{rule}` | rule name, sanitized |
| `{mtime:%Y-%m}` | `strftime` format on mtime; format may not contain `/` |

Rules:
- Substituted values are sanitized: path separators, NUL, and a value of `.` or `..` are rejected (the file becomes `BLOCKED: DEST_INVALID`).
- The result is made absolute and normalized (`os.path.normpath`) **without** resolving symlinks at decision time.
- `static_root` = the path up to the first component containing a placeholder; it drives the `create_destinations` behavior and config validation.
- Unknown placeholders are config errors (with did-you-mean).

## 8. `rules test` output

```
$ clutterxp rules test "SL-CTF-writeup.pdf"
File: SL-CTF-writeup.pdf   (ext=pdf, 0 bytes, mtime=...)
tokens(raw)   : sl ctf writeup
tokens(camel) : sl ctf writeup

 ✓ SecLeaf        priority 10   keyword 'sl' (token)            -> ~/Projects/SecLeaf
 ✗ ThunderCipher  priority 20   no keyword matched
 ✗ College        priority 30   keyword 'lab' not found

Result: MATCH  SecLeaf  ->  ~/Projects/SecLeaf
```

`--json` returns the same data (`RuleTrace` list plus the `Decision`). `rules test --file PATH` stats a real file so size/age conditions can be evaluated.

## 9. Implementation notes for the agent

- Build one `CompiledRule` per rule at load: pre-tokenized keywords, compiled regexes, extension tuple, parsed sizes and durations. `decide` allocates almost nothing.
- Tokenize the file name **once per file** and reuse for all rules.
- Quick reject: if the rule has an `extensions` filter and the file's extension fails, skip before any keyword work.
- Sizes: `KB/MB/GB` are powers of 1024 (`KiB` also accepted); durations: `s m h d w`.
- `older_than`/`newer_than` use a `now_ns` supplied by the caller (testability).
