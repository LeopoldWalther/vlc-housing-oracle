# FEATURE-018 — Feature wordings by digest: the tree no longer quotes the source

**Status:** 🔵 Planned · **Effort:** S (~6h) · **Priority:** High
**Branch root:** `feature/feature-wording-digests` · **Created:** 2026-11-22 · **Updated:** 2026-11-23

> Authored by `architect`. Reviewed by `reviewer` (see `dev/reviews/REVIEW-FEATURE-018.md`).
> Implemented by `implementer` from `dev/plans/technical/FEATURE-018-technical-plan.yaml`.

## Objective

No file of the current tree holds a feature wording of the source in clear. The registry keeps
every wording as a SHA-256 digest, tests and the synthetic generator use invented wordings, and
the leak guard refuses a source wording that is written out again. A reader of the repository can
then no longer identify the source by searching for one of its phrases.

## Context

- **Current state:** the registry `KNOWN_PATTERNS` lists 99 wordings of the source in clear with
  their target (`src/silver/feature_patterns.py:25`), next to one wording that is already listed
  by digest because the leak guard refuses one of its words (`src/silver/feature_patterns.py:129`).
  Both rules match by digest only: `registry.pattern_sha256 = sha256(...)`
  (`src/silver/sql/feature_pattern.sql:42`, `src/silver/sql/listing_feature_label.sql:16`), and
  `registry_rows()` hashes the clear keys before they reach DuckDB
  (`src/silver/feature_patterns.py:142`). The clear text is therefore not needed at run time.
- **Problem:** several wordings are the source's own multi-word phrasing, and the module says so
  ("check that it is the portal's wording", `src/silver/feature_patterns.py:11`). A search for
  one exact phrase leads from the repository to the source. The name of the source is kept out of
  the repository by the leak guard; its wordings are not. The same wordings also stand in clear in
  the synthetic generator (`src/synthetic/landing.py:94`), in four test modules
  (`tests/test_silver_feature_label.py`, `tests/test_silver_feature_pattern.py`,
  `tests/test_silver_feature_registry.py`, `tests/test_synthetic_landing.py`), in the runbook's
  example (`docs/silver-rebuild.md:311`) and in the records of FEATURE-010
  (`dev/plans/technical/FEATURE-010-technical-plan.yaml` with 66 of them,
  `dev/plans/implementations/FEATURE-010-feature-labels.md` with 19).
- **Constraints:** the repository is public and its history is too. This feature cleans the tree;
  it does not remove the wordings from earlier commits (see Open questions & risks). What the rules
  write
  must not change: every digest keeps its target.

## Scope

- **In scope:** the registry by digest; invented wordings for the synthetic generator and the
  tests; the runbook's procedure and example; the FEATURE-010 records; a phrase check in the leak
  guard that keeps the tree clean.
- **Out of scope:** rewriting git history; prose that describes how the source behaves without
  quoting it (padding cards, approximate coordinates); the landing contract, which holds no
  wording; moving the registry to the acquisition side (the review's wider point that
  source-specific knowledge sits on both sides of the contract).
- **Users / consumers:** the operator who adds a wording after the `UnknownFeaturePatterns` alarm;
  every reader of the public repository.

## Dependencies

- **Needs:** nothing.
- **Unblocks:** FEATURE-022 — the table registry moves this module once, in its final form.

## Open assumptions

- **Assumption:** multi-word wordings identify the source, single common words do not. —
  **Evidence:** unvalidated by a search on purpose; the registry's own docstring calls the keys
  the source's wording, and the composite ones (a slash-joined condition, a full sentence about
  access) are not generic vocabulary. — **If wrong:** the feature costs six hours and the tree is
  merely tidier.
- **Assumption:** a phrase check over windows of three to ten words adds seconds, not minutes, to
  the leak guard. — **Evidence:** the tree has 190 tracked files (`git ls-files`); unvalidated as
  a measurement. — **If wrong:** task 2.2 restricts the check to changed files in CI and keeps the
  whole tree for the unit test.

## Design

- **Primary approach:** split the registry by origin. `SOURCE_PATTERN_DIGESTS` maps the SHA-256
  of a source wording to its target and holds all 100 entries. `SYNTHETIC_PATTERNS` maps invented
  wordings in clear to their target; the synthetic generator and the tests use only these.
  `FURNISHING_VALUES` is keyed by digest for source wordings and by clear text for synthetic ones.
  `registry_rows()` returns both sets, so a run over synthetic data and a run over real data use
  the same registry and neither reports the other's wordings as unknown.
- **Boundaries and data flow:** unchanged. The registry is still registered into the DuckDB
  session by `register_feature_registry`, and the two rules still join on `pattern_sha256`.
- **Contracts and invariants:** the set of `(pattern_sha256, target, label_value)` rows for source
  wordings is identical before and after; task 1.1 pins it by a digest of the sorted rows before
  anything moves. No `transformation_version`, no backfill: no rule writes anything different.
- **The clear text stays readable where it is private:** the Silver table `feature_pattern` holds
  every wording with its target. The operator reads wordings there and takes the digest from the
  same query (`SELECT sha256(pattern), pattern, listings ... WHERE target IS NULL`).
- **The phrase check:** `dev/tools/check_leaks.py` gains a second pass. For every line of every
  scanned file it normalises each window of three to ten whitespace-separated words the way
  `feature_wording` does (trim, lower-case, numbers to `#`, surrounding quotes and punctuation
  stripped) and refuses the line when the window's digest is a source digest. Windows start at
  three words, so a common one- or two-word wording never fires.
- **OOP / SOLID:** pure functions and two dictionaries. Nothing has state or variants.
- **Patterns:** None.
- **Deliberately rejected complexity:** injecting a registry into the rule functions so tests can
  pass their own (more plumbing for the same effect); an English gloss per digest (the target
  column already says what a wording means; a gloss is written only for `unread` targets); a
  private side file with the clear wordings (the `feature_pattern` table is that file).

## Approach

### Phase 1 — The registry by digest
- [ ] **1.1 Pin today's registry.** RED: `tests/test_silver_feature_registry.py` asserts the
  SHA-256 over the sorted `pattern_sha256|target|label_value` lines of the 100 source rows
  against a constant computed from the present registry. It passes at once; it is the
  characterisation that the next tasks must keep green. Validation-first, no production change.
- [ ] **1.2 Invented wordings.** RED: a test asserts that `SYNTHETIC_PATTERNS` exists, covers
  every label target, the three furnishing values, one detail column and `unread`, and that the
  synthetic landing's feature strings normalise to keys of it; it fails because the name does not
  exist. GREEN: add the dictionary, switch `src/synthetic/landing.py` and the four test modules
  to invented wordings. The number-normalisation tests keep their numbers and lose the source's
  nouns.
- [ ] **1.3 Source wordings by digest.** RED: a test asserts that the module has no
  `KNOWN_PATTERNS` and that every key of `SOURCE_PATTERN_DIGESTS` is 64 hex characters; it fails
  on the present module. GREEN: replace the 99 clear keys by their digests, merge the one
  existing digest entry, key the source furnishing values by digest. The pin of 1.1 stays green.

### Phase 2 — The tree
- [ ] **2.1 Records and runbook.** Validation-first: a throwaway script lists every file line
  that holds a source wording of three or more words. Replace each in the FEATURE-010 technical
  plan and implementation note by the first eight hex characters of its digest, and the runbook's
  example by an invented one. Rewrite the runbook's "add a wording" procedure: read the digest
  from `feature_pattern`, add it to `SOURCE_PATTERN_DIGESTS`, comment the meaning only for
  `unread`. The script then lists nothing; `python dev/tools/validate_workflow.py` passes.
- [ ] **2.2 The phrase check.** RED: `tests/test_check_leaks.py` gives `find_leaks` a text whose
  third line holds a four-word phrase with a number and a digest set that holds the phrase's
  normalised digest, and expects line 3; it fails because the function knows tokens only. A
  second test expects no finding for a two-word phrase. GREEN: the window pass, fed with
  `SOURCE_PATTERN_DIGESTS`. `python dev/tools/check_leaks.py` then exits 0 on the tree.
- [ ] **2.3 Guard the guard.** RED: a test writes a temporary file with a source wording
  reconstructed from a synthetic digest set and expects exit code 1 from the command; GREEN if
  2.2 already satisfies it, else the smallest fix. Record the check's run time on the whole tree
  in the implementation note.

## Files

- **Change:** `src/silver/feature_patterns.py` — digests for source wordings, clear text for
  synthetic ones.
- **Change:** `src/synthetic/landing.py` — invented feature strings.
- **Change:** `dev/tools/check_leaks.py` — the phrase pass.
- **Change:** `docs/silver-rebuild.md` — example and procedure of `feature_pattern`.
- **Change:** `dev/plans/technical/FEATURE-010-technical-plan.yaml`,
  `dev/plans/implementations/FEATURE-010-feature-labels.md` — wordings replaced by digest
  prefixes.
- **Tests:** `tests/test_silver_feature_registry.py`, `tests/test_silver_feature_label.py`,
  `tests/test_silver_feature_pattern.py`, `tests/test_synthetic_landing.py`,
  `tests/test_check_leaks.py`.

## Test strategy

- **Unit:** the registry pin; the shape of both dictionaries; the phrase pass with a hit, a
  two-word miss, a phrase split over two lines (no finding, documented) and a phrase inside
  quotes and a table cell.
- **Contract / fixtures:** the synthetic landing with invented wordings still produces every label
  value and one unknown wording, as today.
- **Integration:** the existing run over the synthetic landing (`tests/test_silver_rebuild*.py`)
  stays green without edits beyond wordings.
- **Retries / idempotency / evolution:** N/A; no table changes.
- **Configured quality checks:** `python -m unittest discover -s tests -t .`,
  `ruff check src tests dev`, `ruff format --check src tests dev`,
  `python dev/tools/check_leaks.py`, `python dev/tools/validate_workflow.py`.
- **Manual:** after the next AWS run, `UnknownFeaturePatterns` is unchanged from the run before.

## Operational and compliance impact

- **Data sourcing / legal:** the tree stops quoting the source. Nothing crosses the contract.
- **Privacy / security:** none; a digest of a short phrase can be guessed, as the leak guard's own
  docstring says. The aim is that a search does not find it, not secrecy.
- **Observability:** unchanged. A wrong digest shows as a rise of `UnknownFeaturePatterns`, which
  already alarms.
- **Failure and recovery:** a mistyped digest leaves its wording unknown and the alarm mails;
  the fix is a one-line registry change, no backfill.
- **Migration / compatibility:** the image must be rebuilt and deployed for the registry to take
  effect; an operator step with the owner's approval, $0.

## Estimated monthly cloud cost

$0/month — no resources added or changed.

## Success criteria

- [ ] `python dev/tools/check_leaks.py` exits 0 and refuses a file that writes out a source
  wording of three or more words
- [ ] The digest of the source registry rows is the one pinned before the change
- [ ] No test and no line of `src/synthetic/` holds a source wording
- [ ] The first AWS run after deployment reports the same `UnknownFeaturePatterns` as the one
  before
- [ ] Every behavior was implemented test-first and focused plus affected suites pass
- [ ] Configured type, lint, format, infrastructure, and workflow checks pass
- [ ] Data, operational, migration, and cost contracts are documented where affected
- [ ] No speculative scope, secrets, personal data, or generated clutter is introduced

## Open questions & risks

- **Question (owner, any time after this feature): what happens to the history?** The wordings
  stay in the commits of `main` since FEATURE-010 and in the diffs of the pull requests that
  brought them. Three answers, each needing this feature first:
  - *Leave it.* The tree and every search on the default branch are clean; the wordings are
    found only by someone who reads old commits or old pull requests. Cost: nothing.
  - *Rewrite and force-push.* Every commit id since FEATURE-010 changes and parked branches are
    rebased. The old commits stay reachable on the hosting side through the merged pull
    requests, whose diffs still show the wordings, until its support removes them. Incomplete.
  - *A new repository from the rewritten history,* the present one made private. Complete on
    the hosting side; pull requests, their reviews and the CI history are lost, the commits and
    their messages stay. The repository has no forks today.
  Recommendation: leave it if the aim is that a reader does not come across the source; take the
  new repository if the aim is that nobody can establish the source from what is published.
- **Risk:** a reviewer can no longer read the registry. — *Mitigation:* the target names the
  meaning, the private `feature_pattern` table shows wording and target side by side, and the pin
  of 1.1 proves the conversion changed nothing.
- **Risk:** the phrase check fires on ordinary Spanish in a future document. — *Mitigation:*
  windows start at three words and must equal a whole wording; the finding names the line.

## Progress log

- **2026-11-22** — Plan written after the architecture review found the source's wordings in
  clear in the public tree.
- **2026-11-23** — The question of the history is open again, with three options.
