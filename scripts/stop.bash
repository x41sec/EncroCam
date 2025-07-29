#!/usr/bin/env bash

cd -- "$(dirname "$(readlink -f "$0")")"
source common.sh

screen_session_name="$(getConfig 'screen_session_name')"

pkill -f record.py
pkill -f sync.py

if [ ! -z "$(screen -ls | grep "${screen_session_name}")" ]; then
	screen -X -S "${screen_session_name}" quit
fi

if [ "$1" == "-y" ]; then
	REPLY=y
else
	if [ "$1" == "-n" ]; then
		REPLY=n
	else
		read -r -n 1 -p "${questionColor}Also unmount the encrypted partition? [Y/n] "
		echo "${resetColor}"
	fi
fi
if [ "${REPLY}" != "n" ]; then
	encrocam_homedir="$(getConfig 'encrocam_homedir')"
	sudo umount "${encrocam_homedir}/encrypted_mountpoint"

	luks_device_name="$(getConfig 'luks_device_name')"
	if [ -e "/dev/mapper/${luks_device_name}" ]; then
		sudo cryptsetup luksClose "${luks_device_name}"
	fi
fi

