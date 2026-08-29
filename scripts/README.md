# Auxiliary scripts

These scripts solve environment/history problems that do not belong behind a Python API.

- `setup-lake-cache.sh` bind-mounts a local `.lake` cache over a repository that lives on a slow/shared filesystem without replacing the repository's real `.lake` with a symlink. It requires ordinary Unix mount tools and `sudo` for the bind mount.
- `run-git-of-theseus.sh` runs the optional `git-of-theseus` history analysis and renderers, using an installed command or `uv tool run`.

The core census, audit, policy, graph, and reporting workflows are installed as Python console commands.
