# FEATURE-019 — Pinned deployment: one commit, one digest, one reviewed plan

**Status:** 🔵 Planned · **Effort:** M (~9h) · **Priority:** Medium
**Branch root:** `feature/pinned-deployment` · **Created:** 2026-11-22 · **Updated:** 2026-11-22

> Authored by `architect`. Reviewed by `reviewer` (see `dev/reviews/REVIEW-FEATURE-019.md`).
> Implemented by `implementer` from `dev/plans/technical/FEATURE-019-technical-plan.yaml`.

## Objective

What runs in AWS is identified by content, not by a name that can be moved: the image by digest,
the build's actions by commit, the base image and every Python package by exact version. A
deployment is a reviewed plan made from a clean commit of `main`, and every switch the runbook
turns is a Terraform variable. The feature costs nothing a month.

## Context

- **Current state:**
    - One variable names the image of both task definitions (`infra/silver/ecs.tf:188`,
        `infra/silver/labels.tf:113`). Its validation accepts a commit tag of 7 to 40 hex digits
        or a digest (`infra/silver/variables.tf:22`); the runbook tells the operator to set the
        tag (`docs/silver-rebuild.md:680`).
    - The image workflow pushes the tags `<commit>` and `latest` on every push to `main`
        (`.github/workflows/image.yml:52`); a second run on the same commit moves the tag.
    - The base image is the floating `python:3.12-slim` (`Dockerfile:6`) while CI runs 3.12.12.
        `requirements.txt` pins four packages exactly; what they pull in is resolved at build
        time (`Dockerfile:18`, `.github/workflows/ci.yml:33`).
    - `tests/test_tooling_pins.py` pins the ruff version, the commands CI must run and the runner
        image of `ci.yml`. It reads no `uses:` line, no Dockerfile and not `image.yml`.
    - `handoff/` is untracked and not ignored: `git status` lists it and `.gitignore` has no
        entry, so `git add -A` stages it and only the leak guard stands in the way
        (`dev/tools/check_leaks.py:59`).
    - Deployment is `terraform plan` and `apply` by hand (`docs/silver-rebuild.md:706`). The
        queue consumer's zip is built at plan time from the file in the working tree
        (`infra/silver/queue.tf:53`). CI validates offline only (`.github/workflows/ci.yml:72`).
- **Problem:** a tag, a major version and a floating base image let the deployed bytes change
    under an unchanged configuration. An apply uploads whatever `consumer.py` the checkout
    holds, committed or not, and nothing records which commit that was. The backfill procedure
    turns a Terraform-managed attribute by CLI, so an apply in between turns it back.
- **Corrections to the review, checked in the code:**
    - Four third-party actions are pinned to a major tag, not three (`image.yml:32`, `:40`,
        `:47`, `ci.yml:68`); with GitHub's own two there are eight `uses:` lines.
    - This repository has one Lambda, not two (`infra/silver/queue.tf:138`).
    - Exactly one procedure toggles by CLI: the backfill disables and enables the event source
        mapping (`docs/silver-rebuild.md:727`, `:739`) although `consumer_enabled` exists for it
        (`infra/silver/variables.tf:55`, `infra/silver/queue.tf:172`); the rollback section
        allows the same (`:846`). Every other switch is already a variable (`:584`, `:714`,
        `:777`), and the remaining CLI steps touch nothing Terraform manages.
    - A plan file is already used for the first deployment (`:709`); later steps say "apply".
    - Backfill and rollback have written procedures (`:717`, `:844`), and the equality check
        already runs a local backfill of the same landing (`:749`). What is missing is a
        rehearsal before the production run. The names are literals, as the review says
        (`infra/silver/ecs.tf:21`, `infra/silver/main.tf:5`, `src/silver/rebuild.py:42`).
- **Constraints:** the repository is public and holds no AWS credential (`image.yml:3`); its
    Actions logs are public too. One operator, few deployments a month, a bill under $1.

## Scope

- **In scope:** digest-only image reference; actions by commit SHA; base image by digest; a lock
    of transitive versions; `handoff/` ignored; a guard that plans only from a clean commit of
    `main` and records that commit; the backfill switch as a variable; a local rehearsal in the
    image about to be deployed; the runbook and the architecture page.
- **Out of scope:** an apply from CI, a second environment, hashes in the lock (all three in
    Open questions); a scheduled drift check, which needs a credential in CI; a push job that
    alone holds `packages: write` (`image.yml:16`; fork pull requests get a read-only token, and
    after task 1.2 the code that sees the token is fixed); generic leak-guard rules; alarms for a
    switch left off (FEATURE-016); the acquisition repository's half of A5 (planned there).
- **Users / consumers:** the operator who deploys; readers of the public repository.

## Dependencies

- **Needs:** nothing.
- **Unblocks:** FEATURE-021 — its move to landing v4 is a new image plus a backfill, the first
    change that is rehearsed and deployed this way. FEATURE-012 task 4.3 — the labeller's first
    deployment names the image by digest.

## Open assumptions

- **Assumption:** Fargate pulls the public image by the digest the build reports, which names a
    manifest index. — **Evidence:** unvalidated; the variable accepts the digest form but the
    runbook never used it. — **If wrong:** the task fails to start, the failure mail follows, no
    table is touched; task 1.6 then sets the platform manifest's digest.
- **Assumption:** `pip freeze` inside the pinned base image is a complete lock for linux/arm64,
    the platform that runs. — **Evidence:** local pip is 25.0.1, which has no lock command, so
    freeze plus `-c` is what the present tooling offers. — **If wrong:** the build step of task
    1.4 fails on the first difference between the lock and what was installed.
- **Assumption:** the image runs a backfill on a laptop against a SQLite catalog. —
    **Evidence:** same CLI as entry point (`Dockerfile:35`), SQLite extra installed
    (`requirements.txt:5`), catalog set by environment variables (`docs/silver-rebuild.md:87`);
    not run in a container yet. — **If wrong:** task 2.3 finds out on synthetic landing before
    the runbook says so; the fallback is the virtualenv run.

## Design

- **Primary approach:** pin by content and let tests keep the pins. Phase 1 changes
    configuration only. Phase 2 adds one command and one variable.
    - **Image.** The validation accepts `...-silver@sha256:<64 hex>` only. The workflow stops
        pushing `latest`, which nothing reads, and writes commit and digest to the job summary
        for the operator to copy. The commit tag stays for looking an image up.
    - **Build inputs.** Actions by commit, the base image by digest. `constraints.txt` is the
        output of `pip freeze` in that base image after installing `requirements.txt`; the
        Dockerfile and CI install with `-c constraints.txt`, and the image build compares
        `pip freeze` with the file, so a bumped package without a new lock fails its pull request.
    - **Deployment.** `dev/tools/plan_deployment.py` refuses a working tree with any change or
        untracked file and a `HEAD` that `origin/main` does not contain, then runs
        `terraform -chdir=infra/silver plan -out=tfplan -var source_commit=<HEAD>` with the
        operator's further arguments. The operator reads the plan and applies that file, which
        carries its configuration and variables. `source_commit` has no default and becomes the
        consumer's description: each plan shows which commit the zip and the configuration come
        from, and the console answers it later.
    - **Switches.** The backfill is planned with `-var consumer_enabled=false`; the plan after
        it, without the argument, turns the consumer on again. No file edit can be forgotten.
    - **Rehearsal.** Before a backfill or an image with a rule change is applied, the operator
        runs that image by digest on a laptop: synced landing mounted, a SQLite catalog, the
        task's 2 GB as memory limit, `--mode backfill`, and compares counts with the current
        tables. It is the equality check, moved in front of the deployment.
- **Boundaries and data flow:** unchanged. No rule, table or contract is touched.
- **Contracts and invariants:** `silver_image` is a digest; `source_commit` is 40 hex digits.
    No `transformation_version` changes and no backfill is needed: nothing a rule writes changes.
- **OOP / SOLID:** none; one function, with git and Terraform passed in as a callable so the
    test fakes them, as `check_leaks.main` takes its file list.
- **Patterns:** None.
- **Deliberately rejected complexity:** `terraform test` with a mocked provider (a new harness
    for two patterns a Python test can read from `variables.tf`); a lock tool; a Lambda zip
    published by CI; a wrapper around `apply`; commit tags on every resource (noisy plans).

## Approach

### Phase 1 — Pins (ships on its own)

- [ ] **1.1 `handoff/` is ignored.** RED: `tests/test_tooling_pins.py` asserts that
    `git check-ignore -q handoff/probe` exits 0; it exits 1 today. GREEN: `/handoff/` in
    `.gitignore`. The leak guard then stops reading the directory, which is correct: it can no
    longer be staged by accident.
- [ ] **1.2 Actions by commit.** RED (same module, as 1.3 and 1.4): every `uses:` in `ci.yml`
    and `image.yml` ends in `@<40 hex>`; all eight fail today. GREEN: the commit each current
    tag points to, with the release as a trailing comment.
- [ ] **1.3 Base image by digest.** RED: the `FROM` line is `python:<version>-slim@sha256:<64
    hex>` with the version of `.python-version`; today it has neither. GREEN: the index digest
    of that tag, read from the registry.
- [ ] **1.4 Lock of transitive versions.** RED: `constraints.txt` exists, every line is
    `name==version`, the four pins of `requirements.txt` appear with the same version, and the
    Dockerfile and `ci.yml` install with `-c constraints.txt`; the file is missing today.
    GREEN: generate it in the base image of 1.3, allow it in `.dockerignore`, add the `-c`
    argument twice, and add the build step that compares `pip freeze` with the file. Validation:
    `docker build .` passes, and fails after one line of the lock is removed.
- [ ] **1.5 The image by digest only.** RED: `tests/test_deployment_pins.py` reads the
    validation pattern of `silver_image` from `variables.tf` and asserts that it accepts a
    digest reference and refuses a 40-digit tag and `latest`; the tag is accepted today. A
    second assertion: `image.yml` pushes no `latest` and writes the digest to the job summary.
    GREEN: the pattern, the variable's description, the workflow's tags, summary step and header
    comment; the runbook's image bullet and settings row (`docs/silver-rebuild.md:679`, `:692`)
    say digest and how to look one up for a commit already built. `terraform validate` passes.
- [ ] **1.6 Deploy by digest (operator, needs the owner's approval, at most $0.01).** After the
    merge, copy the reference from the image run's summary into the git-ignored tfvars, plan to
    a file, read it (two task definitions, the consumer's environment, the labeller's target),
    apply. Validation: the next Silver task stops with exit code 0 and its description shows
    the digest. Roll back by setting the previous image's digest.

### Phase 2 — Deployment from a commit (after the owner's answer to the first question)

- [ ] **2.1 `source_commit`.** RED: `tests/test_deployment_pins.py` asserts that `variables.tf`
    declares `source_commit` without a default, that its pattern accepts 40 hex digits and
    refuses a short SHA, and that the consumer's `description` in `queue.tf` contains it; none
    exists today. GREEN: the variable and the attribute. `terraform validate` passes.
- [ ] **2.2 `plan_deployment.py`.** RED: `tests/test_plan_deployment.py` with a fake runner:
    a non-empty `git status --porcelain` exits 2 and names the first path; a `HEAD` outside
    `origin/main` exits 2 and names the commit; a clean commit of `main` runs exactly the
    Terraform command above with the extra arguments appended and returns its exit code; in the
    two refusals Terraform is never called. The module does not exist today. GREEN: the command.
- [ ] **2.3 Runbook and architecture page.** RED: `tests/test_deployment_pins.py` asserts that
    `docs/silver-rebuild.md` names no `update-event-source-mapping`, `disable-rule`,
    `enable-rule` or `update-schedule`; the first is there today. GREEN: a section "Deploying"
    (fetch, the command, read the plan, apply the file) that every later "apply" refers to; the
    backfill and rollback sections use `-var consumer_enabled=false`; a section "Rehearsing in
    the image", written after running it once on synthetic landing. `docs/architecture.html`
    (lines 1174, 1222) keeps "manual" and "one environment" and adds what bounds them. Leak
    guard and workflow validator pass.
- [ ] **2.4 First deployment through the command (operator, needs the owner's approval, at
    most $0.01).** Plan with the command, check that the only change is the consumer's
    description, apply. Validation: the function's description shows the commit of `main`.

## Files

- **Create:** `constraints.txt` — exact versions of everything `requirements.txt` installs;
    `dev/tools/plan_deployment.py` — the guard in front of `terraform plan`.
- **Change:** `.gitignore`, `.dockerignore`, `Dockerfile`, `.github/workflows/ci.yml`,
    `.github/workflows/image.yml` — the pins of Phase 1; `infra/silver/variables.tf`,
    `infra/silver/queue.tf` — digest-only pattern, `source_commit`, the consumer's description;
    `docs/silver-rebuild.md`, `docs/architecture.html` — deployment, switches, rehearsal.
- **Tests:** `tests/test_tooling_pins.py` (build inputs, ignore rule),
    `tests/test_deployment_pins.py` (Terraform patterns, workflow tags, runbook),
    `tests/test_plan_deployment.py` (the guard).

## Test strategy

- **Unit:** text-level pins in the style of `tests/test_tooling_pins.py`; the two patterns are
    read from `variables.tf` and applied to accepted and refused examples; the guard's three
    outcomes with git and Terraform faked. Synthetic strings only, no real digest.
- **Integration:** `docker build .` in the image workflow on every pull request, which now also
    proves the lock complete.
- **Retries / idempotency / evolution:** a second plan from the same commit shows no change.
- **Configured quality checks:** `python -m unittest discover -s tests -t . -v`,
    `ruff check src tests dev`, `ruff format --check src tests dev`,
    `python dev/tools/check_leaks.py`, `python dev/tools/validate_workflow.py`,
    `terraform -chdir=infra/silver fmt -check` and `validate`.
- **Manual:** tasks 1.6 and 2.4; one rehearsal on synthetic landing in task 2.3.

## Operational and compliance impact

- **Data sourcing / legal:** N/A.
- **Privacy / security:** the image's supply chain is fixed by content, and the token that may
    publish packages is seen only by pinned code. `handoff/` cannot be staged by accident. No
    credential enters the repository.
- **Observability:** the task description shows the image digest, the consumer's description
    the deployed commit. Drift is seen at the next plan, not before.
- **Failure and recovery:** a wrong digest fails at task start and touches no table; the
    previous digest restores it. A refused plan changes nothing.
- **Migration / compatibility:** the operator's tfvars move from tag to digest once (task 1.6);
    until then a plan is refused with the validation's message.

## Estimated monthly cloud cost

$0/month — no resource is added; two task definitions get a new revision and one Lambda a
description. For comparison, the options this plan does not take:

| Option | Pricing basis | Assumption | Est. / month |
|---|---|---|---|
| Guard, plan file, local rehearsal (recommended) | — | laptop | $0.00 |
| Apply from CI through OIDC | OIDC provider and role are free | Actions minutes free | $0.00 |
| Rehearsal namespace in the same account | Fargate ≈ $0.05 per task-hour with its address | a 4-minute backfill, one copy of Silver per run | < $0.01 idle, ≈ $0.01 per run |
| Second instance of `infra/silver` | three alarms, two custom metrics, Fargate | off: nothing; mirroring every commit: today's bill again | $0.00 to ≈ $1.40 |
| **Total (this plan)** | | | **$0.00/month** |

- **Cost drivers & cheaper alternatives:** none; the recommended design is the cheapest. Money
    does not separate the options (basis: `docs/silver-rebuild.md:782`, the 4 min 6 s backfill
    in `dev/plans/implementations/FEATURE-011-listing-location.md`); work and risk do.
- **External / non-AWS costs:** none; the registry is free for a public image.
- **Budget check:** yes.

## Success criteria

- [ ] Both task definitions name the image by digest, and a tag is refused by validation.
- [ ] No `uses:` line, base image or installed package can change without a commit, and
    `git status` no longer lists `handoff/`.
- [ ] The consumer's description names the commit of `main` it was deployed from.
- [ ] The runbook turns no Terraform-managed attribute by CLI and applies only saved plans.
- [ ] Every behavior was implemented test-first and focused plus affected suites pass
- [ ] Configured lint, format, Terraform, leak-guard and workflow checks pass
- [ ] No speculative scope, secrets, personal data, or generated clutter is introduced

## Open questions & risks

- **Question — how to deploy.** Recommended: the guard, a saved plan and variables for switches
    (Phase 2): it removes the verified risks at $0 and keeps the repository free of credentials.
    Alternatives: (a) apply from CI through OIDC. Here the plan output would put account and
    bucket names into public logs, so it would live in the acquisition repository, with a
    standing role that may write IAM; it adds an approval gate and a scheduled drift check,
    which few deployments by one person do not need yet. (b) No command, only a runbook rule to
    deploy from a detached worktree: two hours less, checked by nobody. Owner: user.
- **Question — where to rehearse.** Recommended: locally in the image (task 2.3). The one
    destructive step is a backfill; it can be rerun, earlier snapshots stay
    (`docs/silver-rebuild.md:854`), and IAM or trigger mistakes fail loudly without touching
    data. Alternatives: (a) a second Glue database and prefix in the same account with a
    `--namespace` argument: about four more hours, under $0.01 a month. It adds Glue, S3 and the
    task's real memory, not the trigger chain, and would be enough; best decided with
    FEATURE-020, which a rehearsed rollback needs anyway. (b) A second instance of the stack:
    every literal name becomes a variable in Terraform and code, for $0.00 to about $1.40 a
    month; not justified at this size. Owner: user.
- **Question — who renews the pins.** Recommended: a Dependabot configuration for
    `github-actions` and `docker`, grouped and monthly, as one more task of Phase 1; Python stays
    by hand because the lock is regenerated in the image. Without it the base image gets no
    patches until someone renews the digest. Owner: user.
- **Question — hashes in the lock.** Recommended: no. A published file of a version cannot be
    replaced on the package index, so versions give the reproducibility; hashes would add
    protection against a tampered index at the price of a lock tool. Owner: user.
- **Risk:** the guard is bypassed by a plain `terraform plan`. — *Mitigation:* `source_commit`
    has no default, so the plain command stops and asks; the runbook names one command.

## Progress log

- **2026-11-22** — Plan created from the architecture review of 2026-11-22 (items A5 public
    half, A7, E first and second bullet), each claim checked against the code.
