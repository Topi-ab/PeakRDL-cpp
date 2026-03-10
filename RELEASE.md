# Release Guide

This document is for maintainers publishing `peakrdl-cpp` to PyPI.

## Publishing Model

- Distribution target: PyPI (`peakrdl-cpp`)
- Auth model: PyPI Trusted Publisher via GitHub Actions OIDC
- Workflow: `.github/workflows/publish-pypi.yml`

## One-Time Setup (PyPI)

1. Log in to PyPI.
2. Create project `peakrdl-cpp` (or configure pending publisher for first publish).
3. Configure Trusted Publisher:
   - Owner: `Topi-ab`
   - Repository: `PeakRDL-cpp`
   - Workflow: `publish-pypi.yml`
   - Environment: leave empty (unless workflow adds one)

Reference page:
- `https://pypi.org/manage/account/publishing/`

## Preflight Checks (Local)

Run from repo root:

```bash
. .venv/bin/activate
pip install -e ".[dev]"
repo_dir="$(pwd)"
cd ..
python -m build --sdist --wheel --outdir "$repo_dir/dist" "$repo_dir"
python -m twine check "$repo_dir"/dist/*
cd "$repo_dir"
```

Optional sanity test:

```bash
.venv/bin/pytest -q
```

## Release Steps

1. Bump `version` in `pyproject.toml`.
2. Commit and push.
3. Create and push tag (example `v0.1.0`):

```bash
git tag v0.1.0
git push origin v0.1.0
```

4. Create GitHub Release from the tag.
5. The publish workflow runs automatically on release publish.

## Troubleshooting

If publish fails with `HTTPError: 400 Bad Request`:

1. Open GitHub Actions logs for the publish job.
2. Check the `Publish to PyPI` step output (workflow runs with `verbose: true`).
3. Confirm version has not already been uploaded.
4. If version exists or filename is reused, bump version and re-release.

Verify live PyPI state:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://pypi.org/pypi/peakrdl-cpp/json
```

- `404` means project/version not visible yet.
- `200` means project exists; inspect releases before retrying.
