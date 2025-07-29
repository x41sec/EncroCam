# Not meant to be executable by itself, so no shebang

questionColor="$(echo -ne '\e[1;36m')"
stepColor="$(echo -ne '\e[1;35m')"
resetColor="$(echo -ne '\e[0m')"

unencrypted_config_file='config_unencrypted'

function getConfig {
	# Search for a line starting with "$1" followed by an equals sign
	result="$(grep "^$1=" "${unencrypted_config_file}")"
	if [ $? -eq 0 ]; then
		echo -n "${result}" | cut -d "=" -f "2-"
	else
		false
	fi
}

function setConfig {
	if ! (getConfig "$1" > /dev/null); then
		# We could add code to dynamically create it, but in the current setup this should simply not happen so better let it be an error
		echo "Error: Configuration key '$1' not found, so it cannot be set."
		return 1
	fi
	# Use a control character as separator (normally: s/a/b/) because these are not allowed in the config file
	sed -i "s^$1=.*\$$1=$2" "${unencrypted_config_file}"
	return 0
}
