# Releasing `multitool-mcp`

Releases are published from GitHub Actions to PyPI with Trusted Publishing. No
long-lived PyPI token is stored in GitHub.

## One-time setup

1. Create a PyPI account, verify its email address, and enable two-factor
   authentication: <https://pypi.org/account/register/>.
2. In the GitHub repository, create an environment named `pypi` and configure
   required reviewers so production publishing requires approval.
3. Register a pending publisher at
   <https://pypi.org/manage/account/publishing/> with these values:

   - PyPI project name: `multitool-mcp`
   - GitHub owner: `we-devices`
   - GitHub repository: `multitool-mcp`
   - Workflow name: `publish-to-pypi.yml`
   - Environment name: `pypi`

The pending publisher creates the PyPI project during the first successful
publish.

## Publish a release

1. Update `version` in `pyproject.toml` and the version reported by the MCP
   server in `src/dut_mcp/server.py`.
2. Run the tests and build checks locally:

   ```shell
   python -m pip install --upgrade build pytest twine
   python -m pytest
   python -m build
   python -m twine check dist/*
   ```

3. Commit the version change, then create and push a matching tag:

   ```shell
   git tag -a v0.1.0 -m "Release 0.1.0"
   git push origin v0.1.0
   ```

4. Approve the `pypi` environment deployment in GitHub Actions. After the
   workflow completes, verify the release at
   <https://pypi.org/project/multitool-mcp/>.

PyPI does not permit replacing a file for an existing version. If a release
needs a correction, increment the version and publish a new tag.
