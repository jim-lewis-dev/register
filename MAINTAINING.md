# Source review and maintainer updates

`export.py` and `update.py` are optional development tools kept in the project folder. They exchange project source for review and apply a patch prepared for a particular snapshot. They do not import or export your saved registers, and `update.py` is not a general GitHub release updater. Ordinary users can update a clone with `git pull --ff-only`.

## Export the current source

Run from the project folder:

```bash
python3 export.py
```

This creates `register-snapshot.zip` beside the script. It includes current source files, uncommitted changes, and `SNAPSHOT.txt` with the branch, HEAD, source status, and recent commit summaries. The Git database and full history are excluded. With Git, tracked files and nonignored untracked files are considered; without Git, the exporter scans the project folder. It makes no commit and edits no source files.

Generated Register archives, runtime caches, virtual environments, register data, SQLite files, and common secret-file names are excluded. Review anything else you added to source before sharing the ZIP: these exclusions are not a general secret scanner. Source modes and internal relative symlinks are preserved. If files change during export, it reports an error and keeps the previous snapshot.

## Apply a snapshot-specific update

Commit or remove local changes first. The project must already be its own Git repository with a commit and a configured Git author. Obtain a trusted `register_update.zip` prepared from your current snapshot and save it in `~/Downloads`, then run:

```bash
python3 update.py
```

The updater selects the newest matching download, including browser duplicate names such as `register_update(1).zip` or `register_update (1).zip`. An explicit path works too:

```bash
python3 update.py ~/Downloads/register_update.zip
```

It checks the ZIP manifest, file contents and modes, declared removals, and the current project against the supplied baseline. It refuses uncommitted or untracked work and mismatched source files before changing them. It then applies the patch, runs the included test suite, commits with the supplied message, and deletes that ZIP after success. Only the selected ZIP is deleted; archives outside the configured Downloads directory are kept. A ZIP is executable project code because its tests run during the update, so use a source you trust.

The updater does not run the installer or change shell configuration or system packages. Handled failures before commit keep the ZIP and restore touched files, file modes, and the Git index. If another process creates a commit during the update, that commit is kept and the working tree is left for inspection. Abrupt termination or power loss can also require inspection; the updater is not a recovery journal.

Each successful ZIP update creates a local commit. Mixing this workflow with upstream GitHub pulls can require reconciling the histories; `git pull --ff-only` will refuse when they have diverged. Export the actual current project before requesting the next snapshot-specific patch.

## Shell completion after an update

The installed launcher and completion loader follow the project files. Open a new Bash terminal to load changed completion; ordinary updates need no reinstall. If an earlier installation copied completion instead of using the loader, run `./install.sh` once and open a new Bash terminal to switch. Rerun installation if you move the project.
