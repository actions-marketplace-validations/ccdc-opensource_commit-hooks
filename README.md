This repository contains files that can be used as a github action and local
git hooks.

It does a few checks on source code to ensure compliance with some general
CCDC coding standard.

The commit will be flagged if it includes certain text files with:

* File name that can cause errors on Windows
* CRLF line endings
* NO NOT MERGE or DO NOT COMMIT
* Tabs
* Missing terminating newline for certain files
* Certain C++ #include patterns and std::exception

The commit will also be flagged if the commit message does not include a Jira
ID (unless marked with NO_JIRA), or if the size of new or modiffied files
exceeds a threshold.


# Github action

## Usage
```yaml
- uses: ccdc-opensource/commit-hooks@v6
  with:
    commitMessage: 'The commit message'
```

## Scenarios
### Check files in pull request for merge to main
```yaml
name: Check pull request files
on:
  pull_request
    branches: [ main ]
jobs:
  Pull-request-files-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
        with:
          ref: ${{ github.head_ref }}
          fetch-depth: 0
      - uses: actions/setup-python@v4
        with:
          python-version: "3.7"
      - name: Get the commit message
        run: |
          echo "commit_message=$(git log --format=%B -n 1 ${{ github.event.after }})" >> $GITHUB_ENV
        shell: bash
      - uses: ccdc-opensource/commit-hooks@v6
        with:
          commitMessage: ${{ env.commit_message }}
```

# commit-hooks
You can use this as git hooks for local repositories.

A set of hooks include:
* commit-msg
* pre-commit
* pre-merge-commit

## Setting up
1. Clone this repo
1. `git config --global core.hooksPath <this repo location>/main`

## Recommended settings
### To ensure the line endings are correctly converted:
1. On Windows: `git config --global core.autocrlf true`
1. On other platforms (including WSL): `git config --global core.autocrlf input`
