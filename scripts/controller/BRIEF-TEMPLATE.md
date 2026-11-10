# Implementation brief template

Fill every section before dispatch. The caller selects the model route and
reasoning effort and records route evidence; do not ask the agent to attest to
its own route. The implementer does not review its own work or merge its task
branch into the shared main checkout.

## 1. Goal and non-goals

State one concrete outcome and name the changes that are out of scope.

## 2. Checkout and files

- Repository, branch, worktree path, and declared base commit.
- Exact files expected to change.
- Files that must remain untouched.
- Absolute report and review-report paths.

## 3. Context to read first

Name the exact repository documents, sections, code seams, and reports. Give
the reader the minimum context needed to implement without reopening settled
decisions.

## 4. Contract and oracle

Describe inputs, outputs, error behavior, exit codes, edge cases, and hostile
inputs to reject. Point to the behavioral oracle: tests or observable
behaviors that fail before the change and pass afterward. Do not use a test
that merely asserts implementation shape.

## 5. Work sequence

List steps in order. For each step, name its verification command and expected
result. Add tests for each new safety guard or behavior. Hand-mutate each new
guard once; record whether the test killed the mutation and add coverage for
any survivor.

## 6. Binding rules

- Read the repo `AGENTS.md`, the project guidance, and the controller
  [`rules.md`](rules.md).
- Keep external working notes as scratch. Anything reusable, cited, or decided
  belongs in the repository.
- Use project-owned workflows. Run tests only through a registered
  `tester-unified` `run-gate` lane, under the shared gate lock. Run one lane at
  a time.
- Update user-facing README, DESIGN-GUIDE, and CONSUMERS docs together for a
  changed capability, config key, vocabulary, or compatibility fact.
- Use `git commit --only -- <paths>`. Each commit, including an origin/main
  merge commit, carries exactly one trailer for the caller-selected route:

  | Route | Exact trailer |
  | --- | --- |
  | Codex Luna | `Co-Authored-By: Codex gpt-6-luna <noreply@openai.com>` |
  | Codex DeepSeek or OpenCode | `Co-Authored-By: Codex deepseek-v4.1-flash <noreply@openai.com>` |

  Name the exact route in the brief; never use a generic Codex trailer.
- Do not push, tag, release, or merge the task branch into shared main unless
  the operator explicitly assigned that action.
- As the final content change when required, fetch fresh `origin/main` and
  merge it into the task branch. Then run required gates on a clean tree; each
  gate log must name the final `HEAD`. Any later content change requires a
  gate rerun.
- Put every report at its named absolute path. Do not leave a skeleton or
  promise to write it later.

## 7. Definition of done

State required gate results and test counts, docs, commits, final head, clean
tree, report path, and any unresolved item. Give a file-by-file disposition,
hand-mutation results, and the exact commit-trailer audit.

## 8. Stop conditions

Give mechanical conditions for pausing and escalating: an unavailable
precondition, a failing gate that cannot be run in the supported environment,
or a behavior not defined by the contract. Product decisions should be
recorded as explicit decisions with consequences, not vague escalation.
