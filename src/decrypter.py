#!/usr/bin/env python3

import sys, time, datetime
from EncroCrypt import EncroCrypt

if len(sys.argv) < 4 or '-h' in sys.argv or '--help' in sys.argv:
    print("""
Usage:
  1. {self} <input.encrocam> <output.hls> <verification_fingerprint> [Seek]
  2. vlc output.hls #streamable, can be started after a second of decrypting

<input.encrocam>: the encrypted recording.

<output.hls>: the decrypted file (will be in HTTP Live Streaming format).

<verification_fingerprint>: the fingerprint of the PGP key which the recording
data should be signed with. During the setup, you either chose your own secret
key or one was generated and displayed to you. If you have (and trust) the
encrypted configuration file, you can take the value from <signing_fingerprint>.

Seek: optional; format is YYYY-MM-DDTHH:MM (in your local timezone).
If given, it will decrypt only video data from that time onward. Note that, to
make seeking faster, the timestamps are not verified. Only the in-video time
overlay is authenticated. Malicious storage could thus break the seeking
feature, but you will notice it in the video itself.

If you need a custom GnuPG home directory, set the GNUPGHOME environment
variable.

Example:
  {self} rec.encrocam rec.hls 3A0...98F 2025-01-05T20:22

You can also pass data from stdin and write to stdout or other kinds of pipes:
  ssh myhost 'cat rec.encrocam' | {self} /dev/stdin >(wc -c) 3A0...98F
""".lstrip().format(self = sys.argv[0].split('/')[-1]))
    exit(1)

seek = -1
if len(sys.argv) == 5:
    d = datetime.datetime.strptime(sys.argv[4], '%Y-%m-%dT%H:%M')
    seek = time.mktime(d.timetuple())
elif len(sys.argv) != 4:
    print('Invalid number of arguments, please use --help')
    exit(1)

ec = EncroCrypt(signing_fingerprint=sys.argv[3])

with open(sys.argv[1], 'rb') as infile, open(sys.argv[2], 'wb') as outfile:
    ec.decrypt(infile, outfile, seek)

