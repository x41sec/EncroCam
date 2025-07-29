#!/usr/bin/env bash

source common.sh

encrocam_homedir="$(getConfig 'encrocam_homedir')"
data_location="$(getConfig 'data_location')"
timeout="$(getConfig "sync_restart_after_minutes")"

python3 ../src/record.py "${encrocam_homedir}" "$(getConfig 'signing_key_fingerprint')" "$(getConfig 'encryption_key_fingerprint')" "${data_location}" &

while true; do
	# timeout with some margin. The process should normally exit by itself, but is not designed to be precise to the second
	timeout "$((${timeout}+2))m" python3 ../src/sync.py "${encrocam_homedir}" "${data_location}" "${timeout}"
	status="$?"
	if [ "${status}" -ne 0 ]; then
		echo "$(date) \`timeout sync.py\` exited with status ${status}"
	fi
	sleep 0.1
done

