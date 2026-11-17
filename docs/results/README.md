# Recorded run

Output captured from one run on 2026-11-16: Floci 2.5.0 on arm64 macOS under
Docker, from `make down` (nothing running) through every step below. Nothing here was
edited except to drop Docker's container start-up lines and, in the deploy log, the
per-resource lines of `terraform apply`.

| File | Command |
|---|---|
| `steps.txt` | exit status and wall-clock seconds of each step |
| `02-deploy.txt` | `make deploy` |
| `03-demo.txt` | `make demo` |
| `04-verify.txt` | `make verify` |
| `05-negative-test.txt` | `make negative-test` |
| `06-replay.txt` | `make replay` |
| `07-verify-after-replay.txt` | `make verify` |
| `08-replay-forced.txt` | `make replay-forced` |
| `09-verify-after-forced-replay.txt` | `make verify` |
| `10-status.txt` | `make status` |
| `11-integration-tests.txt` | `make test-integration` |
| `20-unit-tests.txt` | `make test` |
| `21-lint.txt` | `make lint` |
| `22-terraform-fmt-validate.txt` | `make tf-fmt tf-validate` |
| `23-tflint.txt` | `make tf-lint` |
| `24-checkov.txt` | `make security-scan` |
| `25-package-check.txt` | `./scripts/check_package.sh` |
| `loadgen-summary.json` | written by the load generator |

The integration tests run last because they add events of their own, which a later
`make verify` would rightly report as objects the load generator did not send.
