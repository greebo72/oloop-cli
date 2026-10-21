# Security Policy

## Supported versions

Only the latest release receives security fixes.

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Report it privately through GitHub:
[Report a vulnerability](https://github.com/greebo72/oloop-cli/security/advisories/new)

Please include:

- what the problem is and which version is affected
- steps to reproduce it, or a small example file
- the impact you expect, if you have an idea

This is a hobby project maintained by one person. I will try to respond within
a reasonable amount of time and to fix confirmed problems in a following
release. You will be credited in the release notes unless you prefer not to be.

## Scope

oloop reads and writes Markdown files in a local folder. It has no server and
no network access of its own. Problems that matter here include, for example,
a crafted task file that makes the app read or write outside the data folder,
or that crashes or hangs the app.
