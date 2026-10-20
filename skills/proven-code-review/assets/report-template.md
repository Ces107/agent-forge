# Code review: <PR title or branch>

**Verdict: <BLOCK | REQUEST CHANGES | APPROVE WITH NOTES | APPROVE>** · <n> findings
(<n> proven) · Scope: <base>..<head>, <n> files, <n> lines · Tests: `<command>` <passed | failed | not run>

<Two or three sentences: what the change does, whether it does what the PR claims, and the
single most important thing to fix.>

## Findings

### 1. [CRITICAL · Proven] `<file>:<line>`: <short title>
**What happens:** <concrete trigger> → <wrong outcome for a user, the data or the business>.

**Evidence:**
```
$ <command you ran>
<the decisive lines of real output>
```

**Fix:**
```<lang>
<minimal change>
```

<details><summary>Proof test (add it to the suite)</summary>

```<lang>
<the failing test you ran>
```
</details>

### 2. [HIGH · Traced] `<file>:<line>`: <short title>
**What happens:** <trigger> → <outcome>.

**Evidence:** <the decisive lines, quoted with file:line, and the path that reaches them>.

**Fix:** <minimal change>

## Questions for the author
- <only what you could not decide from the code; three at most>

## Checked and fine
- <what you examined and found correct, one line each>

## Nits
- <at most three; omit the section when there are none>
