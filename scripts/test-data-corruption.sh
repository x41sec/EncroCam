#!/usr/bin/env bash

# $0 file
# N.B. destroys file

fname=$1

#      octal
tr \\0 \\000 </dev/zero | dd of="$fname" seek=900 count=900 conv=notrunc bs=1000
tr \\0 \\001 </dev/zero | dd of="$fname" seek=2500 count=900 conv=notrunc bs=1000
echo __EncroCrypt2 | dd of="$fname" seek=999 conv=notrunc bs=1000
echo __EncroCrypt2 | dd of="$fname" seek=2699 conv=notrunc bs=1000

