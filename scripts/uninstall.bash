#!/usr/bin/env bash

set -e

cd -- "$(dirname "$(readlink -f "$0")")"
source common.sh

encrocam_homedir="$(getConfig 'encrocam_homedir')"

echo 'Calling the stop script to stop any running service...'
"${encrocam_homedir}/scripts/stop.sh" -y

echo 'Removing symlink to startup script...'
startupSymlink="${HOME}/${startup_script_name}"
if [ -L "${startupSymlink}" ]; then
	rm "${startupSymlink}"
fi

# We could prompt whether to wipe $self, but idk, it feels wrong. They might still have data, config,
# or local changes here, and instead just want to remove things installed outside this directory.
# Would rather point them at what to do and make a more informed decision about what gets wiped.
echo 'To remove the code, configuration, and PGP keys in the encrypted partition, run:'
echo "  rm -rf '${encrocam_homedir}'."
echo "To also remove local recording data, see '${data_location}'."

