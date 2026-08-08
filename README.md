# canary

**Point it at the folder where exports get saved. It checks each file as it
lands and tells you what is wrong before you thought to ask.**

Named for the bird. The point was never that it sang — it was that it *stopped*,
early, while there was still time to walk out.

## Why these tools exist

> **Software fails loudly. Data fails quietly.**
>
> A column that stopped updating in March. An export that has been byte-identical
> for six weeks. A nightly job that has "succeeded" every night into an empty
> file. Nothing alerts on any of it, because nothing is *broken* — the numbers
> simply stopped being true, and every dashboard above them kept reporting with
> complete confidence.
>
> **[flatline][] is the judgment**: it decides whether a signal still carries
> information. **canary is the trigger**: it watches the folders where files
> land and asks flatline the moment one changes, so the answer arrives before
> anyone thinks to ask the question.
>
> **Neither tool will ever call a file clean that it failed to read.** A check
> that could not run is reported as a failure to check, never as a pass — in
> code, and with a test, in both tools. Hold us to that one.

## The problem with every tool like this, including ours

They all wait to be asked. You have to already suspect something is wrong, then
go find the file, then remember the tool exists. Which means **the people who
most need the check are exactly the ones who never run it.**

canary inverts the trigger. You save an invoice export at 2:14pm; at 2:15 you
know that the `status` column has been the same value for 400 rows, or that
`notes` is empty for every record, or that a column you rely on stopped being
written three exports ago.

```
canary ~/Downloads ~/Dropbox/exports --report canary.html
```

Local only. There is no endpoint to upload to, because there is no server.

## It does not do the analysis itself

canary calls [flatline][], which does the actual work of deciding whether a
column carries information. That is deliberate: a second copy of that logic
would drift from the first within a month, and **two checkers that disagree are
worse than one checker.**

So canary is a trigger and a report; flatline is the judgment.

### Installing flatline

flatline is on GitHub, not PyPI — the name there belongs to an unrelated Ghidra
tool, which is also why this package is `awllc-canary` rather than `canary`.

```
git clone https://github.com/automatedworkflowllc-design/flatline
pip install ./flatline
```

If you would rather not install it, point canary at a checkout:

```
canary ~/exports --flatline ../flatline
```

If flatline is missing, canary says so and reports every file as
**could not be checked**. It does not report them as clean. If a *different*
package named `flatline` is installed, canary detects that too and tells you
which problem you actually have, rather than blaming your data for a packaging
mistake.

## Three decisions that determine whether you keep reading the report

**Change means content, not timestamp.** Files are compared by hash. A file
re-saved with identical bytes has not changed in any way a business cares about,
and re-reporting it every night is how a tool teaches its owner to ignore it.

**A check that could not run is never reported as clean.** A failed check shows
as ERROR, and the report says in plain words that "could not be checked" is not
a pass. A tool that says *no findings* when it never looked is the exact failure
this exists to catch.

**The exit code reports news, not inventory.** With `--fail-on-findings`, canary
exits non-zero for findings in files it actually re-examined this run.
Everything known stays in the report — nothing is hidden — but a known-bad file
sitting unchanged is not an alarm. A nightly job that reports a problem every
single night gets muted, and a muted guard is indistinguishable from no guard.

A corrupt state file re-examines everything rather than concluding "nothing
changed" forever, and a deleted file stops being reported rather than lingering
as a status nobody can act on.

## Exit codes

| code | meaning |
|---|---|
| 0 | every re-examined file came back clean |
| 1 | a file **could not be checked** — worse than a finding, because it hides an unknown number of them |
| 2 | new findings, with `--fail-on-findings` |

1 outranks 2 on purpose.

## Running it on a schedule

canary is built to run unattended. Give it `--fail-on-findings` so it has
something to say, and wrap it in whatever your scheduler uses. Declaring the
HTML report as a "did this produce output" check would be pointless — the report
carries a timestamp, so it changes on every run whether or not anything
happened. **The exit code is the signal.**

## What it does not see

It only sees files that change on disk. It knows nothing about whether a
scheduled job ran at all, or whether an output went stale between runs. Those
are different questions with different tools ([attest][] and [watchpost][]).
Stating the blind spot is the point: a tool that implies it covers more than it
does is how coverage comes to be assumed instead of checked.

## Tests

```
python -m pytest test_canary.py -q
```

The suite pins the behaviours above as law: an unchanged file must stay quiet, a
failed check must never be reported as clean, a file that could not be checked
must be retried rather than left asserting a stale status, a repeat finding must
not re-raise the alarm, and a different package named `flatline` must not be
mistaken for this one.

## The other three

Four small tools, one idea: **software reports success while doing nothing, and
nobody notices for months.** Each answers a different question, and each says
plainly what it cannot see.

| tool | the question it answers | its blind spot |
|---|---|---|
| [attest][] | did this job run, and did it produce what it claimed? | it sees declared outputs, not whether they are *correct* |
| [flatline][] | is this data still carrying information? | it waits to be asked |
| [watchpost][] | did an output go stale between runs? | it watches files, not the work that made them |

They share one hash chain and one signature implementation, imported rather
than copied — two versions of a trust primitive diverge the first time only one
gets fixed.

**[Why there are several of these, and when we will delete one](https://github.com/automatedworkflowllc-design/attest/blob/HEAD/WHY-SEVERAL-TOOLS.md)** — the rule each tool had to pass to exist, the one
overlap that is real, and the date we have committed to settling it.

[attest]: https://github.com/automatedworkflowllc-design/attest
[flatline]: https://github.com/automatedworkflowllc-design/flatline
[watchpost]: https://github.com/automatedworkflowllc-design/watchpost


---

MIT. Built by [Automated Workflow](https://automatedworkflowllc.com).

