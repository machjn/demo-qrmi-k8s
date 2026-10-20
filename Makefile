.PHONY: export-hands-on

# Copy hands-on/ into another repo, e.g. make export-hands-on DEST=../other-repo/hands-on
# -L replaces symlinks (such as module-2/alice-bob/felis -> ../../../cli/felis)
# with the files they point to, since those targets don't exist in the destination.
export-hands-on:
	@test -n "$(DEST)" || { echo "usage: make export-hands-on DEST=<directory>"; exit 1; }
	rsync -aL --exclude .venv --exclude __pycache__ --exclude .env hands-on/ "$(DEST)/"
