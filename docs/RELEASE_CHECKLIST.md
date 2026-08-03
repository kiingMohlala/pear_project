# Release Checklist — PEAR 3.0.0

## Pre-release

- [ ] Version bumped in `VERSION` and `core/version.py`
- [ ] CHANGELOG updated
- [ ] Public API freeze reviewed
- [ ] `python tests/test_basic.py`
- [ ] Full regression: `python ci/run_regression.py`
- [ ] E2E: `python tests/test_e2e_v300.py`
- [ ] Perf baselines: `python tests/test_perf_v300.py`
- [ ] Security review doc signed off
- [ ] Docker image builds
- [ ] Migration tested on sample data dir

## Publish

- [ ] Tag `v3.0.0`
- [ ] Push container image
- [ ] GitHub release notes
- [ ] Verify `/health` on staging

## Post-release

- [ ] Monitor metrics & audit log
- [ ] First backup on production volume
