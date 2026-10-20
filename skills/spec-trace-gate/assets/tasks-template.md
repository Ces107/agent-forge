# Tasks: <feature name>

Each task `T-N` declares the acceptance criteria it `covers:` and the tests that verify it
(`verified_by:`, comma-separated test names exactly as they appear in the test files).

---

## T-1 — <imperative one-line title>
<One or two sentences: what to build and the safest interpretation of the design.>
covers: AC-1
verified_by: test_<behaviour_one>

## T-2 — <imperative one-line title>
<What to build. If the test does not exist yet, say: "Write test_<name> first.">
covers: AC-2, AC-3
verified_by: test_<behaviour_two>, test_<behaviour_three>
