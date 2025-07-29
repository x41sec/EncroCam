#!/usr/bin/env bash

set -e  # exit if something fails

function getfp() {
	echo "$(gpg --homedir "${gpg_homedir}" --with-colons -K | awk -F ':' '/^fpr:/{ fp=$10 } /^uid:/{ if ($10 == "'"${pgp_key_name}"'"){ print fp } }')"
}

# If $data_partition is not empty, then this is the path inside of the partition. Else, this is inside $encrocam_homedir.
default_data_location='recordings'

# This value must be safe as an awk string value as well as for the name field in GnuPG batch script
# Since it's not user input... should be fine. (If someone can change this, they can also e.g. change your .bashrc to capture sudo.)
# If you want to change it, definitely safe would be to use any combination of a-z, A-Z, 0-9 and spaces.
pgp_key_name='EncroCam Video Data Signing Key'

startup_script_name='encrocam-startup'

step_count=6

cd -- "$(dirname "$(readlink -f "$0")")"
source common.sh

if [ ! -f "${unencrypted_config_file}" ]; then
	cp "${unencrypted_config_file}.defaults" "${unencrypted_config_file}"
fi

echo 'Welcome to EncroCam!'
echo 'Note that your progress is saved and you can safely press Ctrl+c at any prompt.'
echo

# loosely show one "step" for every topic where the user typically needs to take action
echo "${stepColor}Step 1/${step_count}${resetColor}"
echo "EncroCam will run as the current user: '${USER}'"
if [ "$(getConfig 'OS_security_has_been_set_up')" == "1" ]; then
	echo 'You previously confirmed that system access is taken care of, skipping prompt...'
else
	echo 'Setting a password for this Linux user prevents video data from being watched or'
	echo 'modified via unauthenticated console access. You can also choose to disable'
	echo 'console logins altogether and use only ssh keys; it is all up to you.'
	echo 'This warning applies for any other user with sufficient privileges such as root.'
	echo 'If you have not yet secured system access, press Ctrl+c and do it now.'
	echo 'Also consider auto-installing security updates using "unattended-upgrades".'
	echo "${questionColor}"
	echo 'Press enter to confirm that system access is taken care of (e.g., the'
	echo "default passwords for ${USER} and root were changed).${resetColor}"
	read
	setConfig 'OS_security_has_been_set_up' '1'
fi

echo 'Testing sudo...'
if [ "$(sudo id -u 2>/dev/null)" != "0" ]; then
	sudo --version >/dev/null 2>/dev/null
	if [ $? -ne 0 ]; then
		echo 'Command "sudo" not found. If your distribution uses a different mechanism,'
		echo 'it will be necessary to adapt the various scripts to use this. Please open'
		echo 'a ticket in that case!'
		echo 'If you do have sudo available, please install it and re-run the installer.'
		exit 2
	else
		echo 'Error: "sudo" seems to exist but root access was not granted just now.'
		echo 'This access is needed for apt, cryptsetup, and mkfs.ext4.'
		exit 2
	fi
fi

if [[ ! "$(groups)" =~ 'video' ]]; then
	echo 'Your user is not part of the "video" group and will likely not have permissions'
	echo 'to access the camera.'
	read -r -n 1 -p "${questionColor}Add the user to video group? [y/n] "
	echo "${resetColor}"
	if [ "$REPLY" == 'y' ]; then
		sudo usermod -a -G 'video' "${user}"
		echo 'Done, but you will need to log out and in again for it to take effect.'
		sleep 0.4
	fi
fi

echo 'Installing/updating dependencies via sudo apt...'
sudo apt update
sudo apt install --no-install-recommends -y coreutils ffmpeg gpg cryptsetup \
	e2fsprogs screen python3 python3-{pycryptodome,gnupg,pyinotify,requests}
echo

encrocam_homedir="$(getConfig 'encrocam_homedir')"
if [ -z "${encrocam_homedir}" ]; then
	encrocam_homedir="$(realpath "$(pwd)/..")"
	echo "Autodetected EncroCam base directory as '${encrocam_homedir}'..."
	setConfig 'encrocam_homedir' "${encrocam_homedir}"
fi

if [ ! -d "${encrocam_homedir}" ]; then
	echo "Creating ${encrocam_homedir}..."
	sudo mkdir -p "${encrocam_homedir}"
	sudo chown "${USER}" "${encrocam_homedir}"
fi

echo "${stepColor}Step 2/${step_count}${resetColor}"
data_location="$(getConfig 'data_location')"
if [ -d "${data_location}" ]; then
	echo "Data location '${data_location}' exists,"
	echo 'so we already set this up. Continuing...'
	echo 'Note: to change this value later, see "scripts/config_unencrypted".'
else
	read -r -n 1 -p "${questionColor}Should the local copy of video data be stored in a custom location? [y/n] "
	echo "${resetColor}"
	if [ "$REPLY" == 'n' ]; then
		setConfig "data_location" "${encrocam_homedir}/${default_data_location}"
		data_location="$(getConfig 'data_location')"
		echo "Okay! You will find video data in '${encrocam_homedir}/${default_data_location}'."
	else
		echo 'Note: if you want to store them on an external hard drive, you will need to set'
		echo 'this up yourself, for example using /etc/fstab.'
		echo 'In which (existing) directory should EncroCam put the encrypted video files?'
		echo "Example: ${HOME}/encrocam-recordings/"
		while true; do
			read -r -p "${questionColor}Full path: "
			echo -n "${resetColor}"
			# Remove any trailing slash to keep the value consistent
			# The example input is shown *with* slash to make it clear it's a directory
			if [ "${REPLY: -1}" == '/' ]; then
				REPLY="${REPLY::-1}"
			fi
			if [ ! -d "${REPLY}" ]; then
				echo 'Error: Directory does not exist, please try again.'
				echo 'If you need to mount or create it, please Ctrl+c and do'
				echo 'that now, then re-run the installer.'
			else
				break
			fi
		done
		echo
		setConfig 'data_location' "${REPLY}"
		data_location="$(getConfig 'data_location')"

	fi
	if [ ! -d "$data_location" ]; then
		echo "Creating the directory and setting permissions for the current user..."
		sudo mkdir "${data_location}"
		sudo chown "${USER}" "${data_location}"
	fi
fi

echo "${stepColor}Step 3/${step_count}${resetColor}"
encrypted_file_path="${encrocam_homedir}/$(getConfig 'encrypted_file')"
luks_device_name="$(getConfig 'luks_device_name')"
if [ ! -f "${encrypted_file_path}" ]; then
	echo 'EncroCam secures the configuration to prevent someone being able to pull out the'
	echo 'SD Card and read things like the server password or signing key.'
	echo 'Please choose a password for this partition. You will need it when starting'
	echo 'EncroCam, and now during the setup you need to type the same password thrice.'

	# Close anything we may have open from a previous installation attempt
	if [ -d "${encrocam_homedir}/encrypted_mountpoint" ]; then
		sudo umount -q "${encrocam_homedir}/encrypted_mountpoint"
	else
		mkdir "${encrocam_homedir}/encrypted_mountpoint"
	fi
	if [ -e "/dev/mapper/${luks_device_name}" ]; then
		sudo cryptsetup luksClose "${luks_device_name}"
	fi

	# Don't use the final location so that, if we get interrupted, it will start this part anew
	tmpfile='/tmp/encrocam_encrypted_storage_setup'
	dd status=none if=/dev/urandom bs=$((1024*1024)) count=32 of="${tmpfile}"
	echo -n "${questionColor}"
	# We may want to use --pbkdf-memory if more people run into "keyslot operation could fail as it requires more than available memory"
	# The --batch-mode option disables password verification. We do not use --verify-passphrase because they need to enter it again for luksOpen anyway
	# Root permissions seem to be needed for all cryptsetup commands as well as mkfs.ext4, even though we are working with a user's local file...
	sudo cryptsetup --batch-mode --type luks2 --pbkdf argon2id luksFormat "${tmpfile}"
	echo -n "${resetColor}"
	echo 'Unlocking the partition for creating the filesystem... (password repeat 2 of 3)'
	echo -n "${questionColor}"
	sudo cryptsetup luksOpen "${tmpfile}" "${luks_device_name}"
	echo -n "${resetColor}"
	sudo mkfs.ext4 -q "/dev/mapper/${luks_device_name}"
	sudo cryptsetup luksClose "${luks_device_name}"

	# Only do this at the end, so if someone aborts and restarts, or if something failed (e.g. typo'd passphrase), they get a fresh try
	mv "${tmpfile}" "${encrypted_file_path}"
else
	echo 'Encrypted partition already configured, continuing...'
fi

startupSymlink="${HOME}/${startup_script_name}"
if [ ! -L "${startupSymlink}" ]; then
	if [ -e "${startupSymlink}" ]; then
		echo 'Installation error:'
		echo 'The following path already exists, but is not a symlink to the startup script:'
		echo "${startupSymlink}"
		exit 3
	else
		# Prefix $(pwd)/ here (even though it should be a no-op for a relative filename) in order to make sure the subsequent symlinking will work
		if [ ! -f "$(pwd)/${startup_script_name}" ]; then
			echo "Missing file: '${startup_script_name}'"
			echo 'The installation expects this to exist in the current directory, which is:'
			pwd
			exit 3
		else
			ln -s "$(pwd)/${startup_script_name}" "${startupSymlink}"
		fi
	fi
fi

# Mount partition both as test and so we can place the GnuPG stuff inside
echo 'Testing the startup script which unlocks the encrypted partition...'
"${startupSymlink}" nostart

# Set permissions so our user (and only our user) has access
sudo chmod go-rwx "${encrocam_homedir}/encrypted_mountpoint/"
sudo chown "${USER}" "${encrocam_homedir}/encrypted_mountpoint/"

echo "${stepColor}Step 4/${step_count}${resetColor}"
signing_key_fingerprint="$(getConfig 'signing_key_fingerprint')"
gpg_homedir="${encrocam_homedir}/encrypted_mountpoint/gpg_homedir"
if [ -z "${signing_key_fingerprint}" ]; then
	mkdir -p "${gpg_homedir}"
	sudo chmod go-rwx "${gpg_homedir}"
	fp="$(getfp)"
	if [ ! -z "${fp}" ]; then
		echo "There already exists a PGP signing key for EncroCam in the encrypted partition."
		read -r -n 1 -p "${questionColor}Use this key with fingerprint ${fp}? [y/n] "
		echo "${resetColor}"
		if [ "${REPLY}" == 'y' ]; then
			setConfig "signing_key_fingerprint" "${fp}"
		else
			fp=""
		fi
	fi
	if [ -z "${fp}" ]; then
		echo "${questionColor}Do you want to supply your own PGP key to sign the video data with?"
		read -r -n 1 -p 'If you choose no, a key will now be generated. [y/n] '
		echo "${resetColor}"
		if [ "${REPLY}" == 'y' ]; then
			echo "Import the key into the encrypted partition, using a command such as:"
			echo "  gpg --homedir '${gpg_homedir}' --import 'key.asc'"
			read -r -p "${questionColor}Enter the fingerprint for this key: "
			echo -n "${resetColor}"
			setConfig 'signing_key_fingerprint' "${REPLY}"
			signing_key_fingerprint="$(getConfig 'signing_key_fingerprint')"
		else
			gpg --homedir "${gpg_homedir}" --batch --generate-key <(cat <<heretag
	%no-protection
	Key-Type: eddsa
	Key-Curve: Ed25519
	Key-Usage: sign
	Name-Real: ${pgp_key_name}
	Expire-Date: 0
heretag
			)
			fp="$(getfp)"
			setConfig "signing_key_fingerprint" "${fp}"
		fi

		echo "!  This is the PGP key to verify the signature of your video data. It is not  !"
		echo "!  secret, but you will want to prevent an attacker from inserting their own  !"
		echo "!  key to falsify the data. Store this key so you know the data is authentic. !"
		echo
		gpg --homedir "${gpg_homedir}" --export --armor "${fp}"
		echo
		echo 'End of key data. In case cannot copy terminal output, you can save the key to'
		echo 'a file by using a command like this:'
		# Don't include the fingerprint here because we just generated the key and so it's probably the only thing in this ${gpg_homedir}.
		# The --armor flag is there just for consistency, so the output looks visually similar and people don't think it did the wrong thing.
		echo "  gpg --homedir "${gpg_homedir}" --export --armor > out.asc"

		echo "${questionColor}Press enter when you've saved the data, or Ctrl+c to run the command above.${resetColor}"
		read
	fi
else
	echo 'PGP signing key already set up, skipping...'
	echo 'In case you need the command again to export the public key data, here it is:'
	echo "  gpg --homedir '${gpg_homedir}' --export --armor > out.asc"
	echo
fi

echo "${stepColor}Step 5/${step_count}${resetColor}"
encryption_key_fingerprint="$(getConfig 'encryption_key_fingerprint')"
if [ -z "${encryption_key_fingerprint}" ]; then
	recipient_key_file='encrypt_for_this_key.asc'
	if [ -f "${recipient_key_file}" ] || [ -f "../${recipient_key_file}" ]; then
		if [ -f "${recipient_key_file}" ]; then
			path="${recipient_key_file}"
		else
			path="../${recipient_key_file}"
		fi
		echo "Found '${path}', importing..."
		gpg --homedir "${gpg_homedir}" --import "${path}"  # this does not output the FP of the key we just imported...
		fp="$(gpg --homedir "${gpg_homedir}" --show-keys --with-colons "${path}" | awk -F ':' '$1=="fpr"{print $10}' | head -1 | tr -d '\n')"
		setConfig 'encryption_key_fingerprint' "${fp}"
		echo "Configured fingerprint: ${fp}"
	else
		echo 'What PGP key should EncroCam encrypt the video data for?'
		echo
		echo 'Note: the previous PGP key was for signing the data (so you can establish its'
		echo 'authenticity); this key is to encrypt the data (so only you can decrypt/watch'
		echo 'it). This is usually *your* PGP public key. Feel free to generate a fresh key'
		echo 'but store it in a durable way such that you can later decrypt the video data.'
		echo
		echo "It's a bit tricky to input a key here, so please import it in another terminal"
		echo "with the following command. You will be asked for the fingerprint next."
		echo "  gpg --homedir '${gpg_homedir}' --import 'public_key.asc'"
		echo "Or, alternatively, place the key in a file called '${recipient_key_file}' in the"
		echo "EncroCam directory (${encrocam_homedir}). When you re-run the installer,"
		echo "it will import the key and automatically detect the fingerprint."
		echo
		read -r -n 1 -p "${questionColor}Import (M)anually or (S)top installer to create a file? [M/S] "
		echo "${resetColor}"
		if [[ "${REPLY}" =~ [Ss] ]]; then
			echo 'Stopping installer as asked. Copy your public key into this file:'
			echo "  ${recipient_key_file}"
			echo 'and then re-run the installer.'
			exit 0
		fi

		while true; do
			read -r -p "${questionColor}Enter the fingerprint for the imported key: "
			echo -n "${resetColor}"
			fp="${REPLY}"
			if [ ! -z "$(gpg --homedir "${gpg_homedir}" -k | grep "${fp}")" ]; then
				break
			else
				echo "Key not found when running \`gpg -k --homedir '${gpg_homedir}'\`,"
				echo 'please try again.'
			fi
		done
		setConfig 'encryption_key_fingerprint' "${fp}"
	fi
	# We need to now mark this key as verified so that GnuPG will allow encrypting data for it. This is done by signing the key.
	# Because the camera's signature is pretty useless, and because we're not asking the user for consent (https://xkcd.com/364/), we use local aka non-exportable signing.
	# Using --no-tty and --quiet and >/dev/null together seems to get it to a state where it'll print only errors
	encryption_key_fingerprint="$(getConfig 'encryption_key_fingerprint')"
	gpg --homedir "${gpg_homedir}" --quiet --no-tty --quick-lsign-key "${encryption_key_fingerprint}" >/dev/null
else
	echo 'Encryption key fingerprint already configured, continuing...'
	echo 'Note: to change this value later, see "scripts/config_unencrypted".'
fi

echo "${stepColor}Step 6/${step_count}${resetColor}"
config_encrypted_path="${encrocam_homedir}/encrypted_mountpoint/config.py"
if [ ! -f "${config_encrypted_path}" ]; then
	echo 'A configuration file will be placed in the encrypted partition where you can'
	echo 'enter FTP details, uptime monitor URL, etc. Once this file exists, EncroCam is'
	echo 'ready to run, but of course it will only store (encrypted) recordings locally'
	echo 'until you configure these things.'
	echo "${questionColor}Press enter to create the configuration file:"
	echo "  ${config_encrypted_path}${resetColor}"
	read
	cp ../src/config_encrypted.defaults.py "${config_encrypted_path}"
else
	echo 'Encrypted configuration file already exists, continuing...'
fi

if [[ "${SHELL}" =~ 'bash' ]]; then
	if [ -z "$(grep 2>/dev/null EncroCam "${HOME}/.bashrc")" ]; then
		echo 'Appending message to .bashrc to remind you upon login how to start EncroCam...'
		# Add this for those who do not know the system already (like a new sysadmin in a company), or even to refresh your own memory after it ran untouched for some years.

		echo "# Added by EncroCam setup. Can be safely removed if you don't want it" >> "${HOME}/.bashrc"
		echo "echo '---------------------'" >> "${HOME}/.bashrc"
		echo "echo 'Greetings, traveller!'" >> "${HOME}/.bashrc"
		echo "echo 'After startup, you probably want to run ./${startup_script_name}'" >> "${HOME}/.bashrc"
		echo "echo 'EncroCam wishes you a pleasant journey with the crypto cybercam.'" >> "${HOME}/.bashrc"
	fi
	echo 'Setup finished! The system is now ready to record.'
	echo 'The easiest way to do a full system test is to reboot. Upon login, a message'
	echo 'should appear telling you how to start EncroCam.'
else
	echo 'Skipped appending message to .bashrc because you do not seem to be using Bash.'
	echo
	echo 'Setup finished! The easiest way to do a full system test is to reboot and,'
	echo "after logging in, use ./${startup_script_name} which will unlock the"
	echo 'encrypted partition and start the camera.'
fi

echo 'To view recording files, see the information in: ./decrypter --help'
echo
echo 'EncroCam wishes you a pleasant journey with the crypto cybercam!'

