#!/usr/bin/env bash
# Build Perch as a Flatpak, export a single-file bundle (Perch.flatpak) and optionally install it.
#   build-aux/build-flatpak.sh            # build + bundle
#   build-aux/build-flatpak.sh --install  # build + bundle + install for the current user
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

app_id="io.github.eboye.Perch"
find perch -name __pycache__ -type d -prune -exec rm -rf {} +

flatpak-builder --user --install-deps-from=flathub --force-clean \
  --repo=.flatpak-repo .flatpak-build "${app_id}.yml"

flatpak build-bundle .flatpak-repo Perch.flatpak "$app_id" \
  --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo
echo "Bundle: ${repo_root}/Perch.flatpak"

if [[ "${1:-}" == "--install" ]]; then
  flatpak install --user -y --reinstall Perch.flatpak
fi
