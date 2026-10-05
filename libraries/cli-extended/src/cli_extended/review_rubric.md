# cli-extended review rubric

You are reviewing one command-line tool built with cli-extended. Judge the help
text, the verbs and the pending review cases below against each checklist item.
For every problem, add an entry to the findings file with a concrete remedy; for
every case, record your decision in the review catalog. Do not invent facts the
bundle does not show; if you cannot tell, say so in the finding.

### Naming and flag consistency

What to look for: the same concept spelled differently across verbs (`--dir` vs
`--directory`), verbs that mix noun-first and verb-first naming, short flags used
for two meanings, flags that differ in case or separator style.
How to record it: one finding per concept, category `consistency`, remedy naming
the single spelling to keep. Severity `major` when two spellings do different
things, otherwise `minor`.

### Help clarity and examples

What to look for: a description that restates the verb name, undefined jargon,
missing examples on verbs with more than one required input, option text that
does not say what happens when the option is omitted.
How to record it: category `help`, remedy with the replacement wording. A verb
that cannot be understood from its help alone is `major`.

### Mutating verbs without confirmation or dry-run

What to look for: verbs that change or delete state (files, services, remote
data) but do not declare `mutating`, have no `--yes` confirmation and offer no
`--dry-run`.
How to record it: category `semantics`, remedy "declare `mutating=True` with
`dry_run=True`" or an explicit reason it is safe. A destructive verb with neither
is `blocker`.

### Read verbs without `--json`

What to look for: verbs that only read and report (list, show, status) but give
no machine-readable output.
How to record it: category `grammar`, remedy "keep `include_json=True` and emit
the result with `runtime.output.primary`". `minor` unless a script consumer is
documented.

### Exit-code meaning

What to look for: failure paths that exit 0, usage errors that exit 1, one code
used for different failures, undocumented codes.
How to record it: category `semantics`, remedy listing the intended code per
failure. A failure that exits 0 is `major`.

### Undeclared option dependencies and conflicts

What to look for: options that only work together, options that silently override
each other, or a value that is only valid for one choice of another option, all
enforced by hand in the handler. These are candidates for declarative W2
constraints (`Requires`, `Conflicts`, `RequiresChoice`).
How to record it: category `grammar`, remedy naming the constraint and its
reason. Note the matching refusal candidate in the catalog.

### Hidden-option justification

What to look for: `hidden=True` options without a stated reason, or hidden
options that are really part of the supported interface.
How to record it: category `consistency`, remedy "document it or justify hiding
it in the catalog rationale". Hidden debugging aids with a reason are fine.

### Positional versus option choice

What to look for: a required value passed as an option (`--name X`) that is the
natural subject of the verb, or an optional modifier passed positionally;
multiple positionals whose order is easy to confuse.
How to record it: category `grammar`, remedy naming the preferred form.

### `configure` callbacks that could be declarative

What to look for: verbs that add arguments or options inside a `configure`
callback that `ArgumentSpec`, `OptionSpec` and constraints can express, so the
surface cannot see them.
How to record it: category `adoption`, remedy "move to declarative specs". If the
surface reports incomplete syntax for the verb, this is `major`.

### Destructive defaults

What to look for: the default value of an option or the behaviour of a bare
invocation that deletes, overwrites or sends something.
How to record it: category `semantics`, remedy "make the safe behaviour the
default and require an explicit flag". `blocker` when data loss is possible.

### Error message actionability

What to look for: errors that state a symptom without the cause or the next step,
messages that leak internals, errors that name no option or value.
How to record it: category `help`, remedy with the improved message. Use
`CliFailure(message, hint=...)` for the next step.
