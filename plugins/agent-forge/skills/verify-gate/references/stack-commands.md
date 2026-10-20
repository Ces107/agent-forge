# Default checks per stack

Use these only when the project does not define its own (CI file, Makefile, package scripts).
Run from the project root unless noted. Every command must already be installed; never install it.

## Python (`pyproject.toml`, `setup.cfg`, `requirements*.txt`)

| Family | Command |
|---|---|
| tests + coverage | `python -m pytest --cov` (threshold from `[tool.coverage]` / `--cov-fail-under` in config) |
| lint | `ruff check .` or `flake8` |
| types | `mypy .` (respects `[tool.mypy]`) or `pyright` |
| security | `bandit -r <package dir> -q` ; `pip-audit` if installed |

## TypeScript / JavaScript (`package.json`)

| Family | Command |
|---|---|
| tests + coverage | `npm test` ; `npx vitest run --coverage` or `npx jest --coverage` if those are the runners |
| lint | `npm run lint` or `npx eslint .` |
| types | `npx tsc --noEmit` |
| security | `npm audit --audit-level=high` (needs the lockfile; it contacts the npm registry, so say so) |

Use the package manager the lockfile implies: `pnpm-lock.yaml` → pnpm, `yarn.lock` → yarn,
`bun.lockb` → bun.

## Go (`go.mod`)

| Family | Command |
|---|---|
| tests + coverage | `go test ./... -race -cover` |
| lint | `golangci-lint run` if installed, else `go vet ./...` |
| types | covered by the compiler in `go test` / `go build ./...` |
| security | `govulncheck ./...` if installed ; `gosec ./...` if installed |

## Rust (`Cargo.toml`)

| Family | Command |
|---|---|
| tests | `cargo test --all-features` |
| coverage | `cargo llvm-cov` or `cargo tarpaulin` if installed |
| lint | `cargo clippy --all-targets -- -D warnings` |
| types | covered by the compiler |
| security | `cargo audit` if installed |

## Java / Kotlin (`pom.xml`, `build.gradle*`)

| Family | Command |
|---|---|
| tests + coverage | `./mvnw -q verify` (JaCoCo if configured) or `./gradlew check` |
| lint / static | Checkstyle, SpotBugs, ktlint or detekt, as configured in the build |
| security | OWASP dependency-check plugin, if configured |

## .NET (`*.csproj`, `*.sln`)

| Family | Command |
|---|---|
| tests + coverage | `dotnet test --collect:"XPlat Code Coverage"` |
| lint | `dotnet format --verify-no-changes` |
| security | `dotnet list package --vulnerable` |

## Any stack: secret scan

`gitleaks detect --no-banner` or `trufflehog filesystem . --only-verified`, if installed.
