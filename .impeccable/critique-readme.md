# Critique — mcgrapeng/attest README.md

- Date: 2026-11-15
- Target: `README.md` (+ `README.zh-CN.md` mirror)
- Mode: Persuade (a tool's landing page)
- Audience: a developer who wants to add a delivery gate to their own repository
- Provenance: **not degraded** — Assessment A (design review) and Assessment B
  (detector + browser evidence) ran as two isolated parallel sub-agents, in
  separate browser tabs.

## 1. Design specificity

The language is entirely attest's own: the four evidence kinds, the
"What it is not" table, the refusal to use verify/certify/sign-off, the exit
codes, "does not block the merge by default". None of it transfers to another
product.

The composition did not. The page was the standard GitHub landing template —
centred wordmark, one bold line, badge row, fenced install command, wide hero
image, H2 sections. Any CLI tool could ship it unchanged.

The sharpest finding: **the only visual asset carrying product personality was
cropped away from the product identity.** The real report prints
`── 未实测 ──` and `── 本工具不判定 ──` before the conclusion; the hero stopped
one line above. The thing a competitor cannot copy was outside the frame.

## 2. Nielsen heuristics (before)

| # | Heuristic | Score | Evidence |
|---|---|---|---|
| 1 | System status visibility | 3 | CI badge, version, "Actively developed", gaps listed |
| 2 | Match with the real world | 2 | Prose excellent; the visual and the sample report are Chinese on an English page |
| 3 | User control and freedom | 3 | Four install/run routes, uninstall documented |
| 4 | Consistency and standards | 2 | Mixed badge styles, empty header cell on the first table, mirror residue on the English page |
| 5 | Error prevention | 4 | "What it is not", exit-code semantics, dependency prerequisite, never deletes real directories |
| 6 | Recognition over recall | 3 | Commands copyable, vocabulary defined in place; but the hero's conclusion line had to be matched against the text 1200px later |
| 7 | Flexibility and efficiency | 2 | Five parallel entry routes, no chooser; the Action's inputs buried in prose |
| 8 | Aesthetic and minimalist design | 2 | Hero mostly empty, seven identical code blocks, `## License` a single word |
| 9 | Error recovery | 3 | Clone fallback, `--dry-run`, uninstall; language scope was nowhere on the page |
| 10 | Help and documentation | 3 | All doc links present; no command behind the headline numbers |
| | **Total** | **27/40** | acceptable, upper band |

## 3. Cognitive load

Four of eight checklist items failed: chunking (14 criteria in one paragraph;
5 Action inputs in one sentence), visual hierarchy (the only CTA renders like
the other six code blocks; H2 is larger than the value proposition),
minimum options (five parallel paths), and working memory (the hero's
conclusion line is Chinese and 1200px from its match).

Decision points above four: the criteria paragraph; the Action inputs; the
five install routes; the "what is it not" table.

## 4. Emotional journey

- Peaks: the "What it is not" table plus the naming rationale; the self-test
  report paragraph.
- Valleys: the hero (Chinese, 45% dead space, ~10px glyphs, cropped before
  the most distinctive section); the three-item gap list whose third entry is
  an internal detail (`node_modules`).
- End: "MIT" — the page ends on its weakest note while its strongest trust
  asset sat ten lines above. Peak-end rule broken.
- Reassurance at the three "is this tool trustworthy?" moments: one was
  well handled (the evidence table), two were open (hero, headline numbers).

## 5. Strengths

1. "What it is not" — four objections dismantled before features; the naming
   rationale is the one line a reader forwards to a colleague.
2. The four evidence kinds, defined at first use and used consistently across
   the table, the sample report and the status section.
3. Publishing weak numbers (trigger recall 0.5, Windows unverified) with exact
   qualifiers — the precondition for every other claim on the page.

## 6. Priority issues

**Fixed in v0.1.5**
1. Hero image removed from the first screen: at GitHub's 838px column it
   rendered 9.9px glyph height (3.8px on a phone), and its alt text disagreed
   with the picture (three sections / two sections / four kinds in the body).
   The asset stays in the repo; the demo now lives in text.
2. Language scope on the page: TypeScript / JavaScript and Python; Rust and Go
   exit with code 2 and say why.
3. Install paths became a chooser table instead of five parallel routes.
4. The Action's five inputs became a table with defaults and a required
   marker.
5. Empty header cell on the first table, mirror residue `(已知遗留)` on the
   English page, and the two language pages could not reach each other.
6. The page's last line is now the evidence (five public repositories,
   including the ones that made the tool look wrong), not the licence.

**Open**
7. The wordmark's dark variant follows the OS preference, not GitHub's theme
   toggle: in GitHub's dark theme the light wordmark loads (≈3.5:1 contrast).
   A platform limitation, not a fixable bug.
8. GitHub's own chrome owns the first screen — the file tree and the About
   panel sit above the README, so the landing page cannot control the
   first viewport.
9. The hero asset has no generator script in the repository; a legible
   replacement needs one.

## 7. Persona red flags

- A developer with a TypeScript monorepo reads "Supported today" and stops —
  good. A Rust developer reads nothing about scope and installs before
  discovering the checker exits 2 — fixed in v0.1.5.
- A reviewer who distrusts marketing sees a hero image they cannot read and
  concludes "the author chose the picture over the evidence" — the opposite of
  the page's intent.
- A screen-reader user gets a Chinese alt text on an English page, describing
  sections that are not in the image — worse than no image.

## 8. Minor observations

- Detector on the raw Markdown returns `[]` with the caveat "findings are an
  undercount, not a clean bill of health" (element rules need HTML/CSS). On
  the rendered page: three paragraphs over ~110 characters, and 17 em-dashes
  in the README body (the reported 23 include six from GitHub's own chrome).
- All 19 links resolve; 0 broken. Heading levels are continuous. All seven
  code blocks scroll horizontally at 390px, including the conclusion block
  (604px → 280px, 46% visible).
- The skills.sh badge renders as "▲ Skills 1" (one install).

## 9. Provocative questions

1. The report ends with `── 本工具不判定 ──`, the fixed list of what the tool
   refuses to judge. It is in the code, in SKILL.md, in docs/criteria.md and
   in every report — and it was outside the one image a stranger sees. Was
   that crop for legibility, or for staging?
2. The README says every number comes from a command you can re-run, and a
   guard checks it. The guard still lets a branch and `main` disagree
   (32/32 vs 27/27) — so what actually keeps the face of the project true?
