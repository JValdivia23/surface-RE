#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USBC_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DOTFILES_SCRIPT="${DOTFILES_SURFACE_DIR:-$HOME/dotfiles/profiles/surface}/scripts/install-usbc.sh"

sudo "${DOTFILES_SCRIPT}" 2>&1 | tee "${USBC_DIR}/dkms_install.log"
echo "${PIPESTATUS[0]}" > "${USBC_DIR}/dkms_install.done"
echo
read -rp "Done! Press Enter to close..."
