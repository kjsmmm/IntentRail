# Release process

## Preflight

1. Confirm that `pyproject.toml`, `distribution/canonical.json`, engine constants, and host manifests use the same semantic version.
2. Run the complete test suite:

   ```text
   python -m unittest discover -s tests -t . -v
   ```

3. Run `npx skills add . --list` and validate all five Skills with the current Agent Skills tooling.
4. Validate the Codex and native host manifests with their current official validators.
5. Complete the native host trust checks and multi-turn product regressions claimed for the release.

## Build

Build the complete GitHub Release asset set from a clean staging copy:

```text
python tools/build_release.py --json
```

The final `dist/` directory must contain only:

- `intentrail-<version>-py3-none-any.whl`;
- `intentrail-<version>.tar.gz`;
- four host-specific ZIP packages;
- `release-manifest.json`;
- `SHA256SUMS.txt`.

The host-archive builder rejects cache directories, bytecode, loose `*.egg-info`, missing runtime files, and version drift. Wheel and source-distribution builds use a clean staging copy so package tooling does not pollute the repository; standard metadata inside the source distribution is expected.

## Verify and publish

1. Recompute every hash listed in `dist/SHA256SUMS.txt` and compare Skill/runtime hashes with `release-manifest.json`.
2. Install the wheel and source distribution independently in fresh uv or pipx environments.
3. Run `intentrail doctor`, a dormant Hook probe, and affected real-host product regressions.
4. Create an annotated `v<version>` tag from the reviewed commit.
5. Push the tag to run the GitHub Release workflow and verify all eight files are attached.
6. Configure the GitHub `pypi` Environment and PyPI Trusted Publisher, then manually run `Publish to PyPI` for the reviewed tag.
7. Verify `uv tool install intentrail` from PyPI before advertising the package-name installation path.

Do not publish `.intentrail/`, `.intentrail-cli`, `.intentrail-install.json`, test environments, build directories, or loose generated `*.egg-info` directories. Setuptools-generated metadata inside the standard source distribution is expected.

Before each public release, confirm that `pyproject.toml` project URLs and ecosystem installation examples still point to the canonical GitHub repository.
