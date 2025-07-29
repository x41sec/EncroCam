#!/usr/bin/env python3

import sys, time, os, fcntl, subprocess
from EncroCrypt import EncroCrypt
sys.path.append('../')
from encrypted_mountpoint.config import *

encrocam_homedir = sys.argv[1]
signing_fingerprint = sys.argv[2]
encrypt_fingerprint = sys.argv[3]
recordings_directory = sys.argv[4]

gnupghome = f'{encrocam_homedir}/encrypted_mountpoint/gpg_homedir'

starttime = None
remainingSeconds = None
while True:
    # Need to reinstantiate because the output file may have changed and this ensures it writes a key packet before any data packets
    ec = EncroCrypt(signing_fingerprint, encrypt_fingerprint, gnupghome)

    stderr = subprocess.DEVNULL
    # To align the files to their supposed start time, compute the remaining time for this time slot
    oddly_short_threshold = 31  # seonds
    if starttime is not None and time.time() - oddly_short_threshold < starttime and remainingSeconds is not None and remainingSeconds > oddly_short_threshold:
        # ffmpeg ran for very little time, but was supposed to run for more than that? Let's see what's happening
        stderr = None  # causes it to be printed

    # To align the files to their supposed start time, compute the remaining time for this time slot
    starttime = time.time()
    seconds_per_file = Config.hours_per_recording * 3600
    remainingSeconds = seconds_per_file - (starttime % seconds_per_file)
    filename = recordings_directory + '/' + timeToFilename(starttime)
    # TODO implement logging like in sync.py, or even use the proper stdin logging with rotation like described in https://stackoverflow.com/a/9107096/1201863
    print(time.strftime('%a %d %b %H:%M:%S %z', time.localtime()) + f' Starting recording for {round(remainingSeconds/3600, 3)} hours to {filename}')

    proc = subprocess.Popen([
        'ffmpeg',
            '-f', Config.input_format,
            '-framerate', str(Config.input_framerate),
            '-video_size', Config.input_resolution,
            '-i', Config.input_device,
            '-vf', r"drawtext = text = '%{localtime\:%Y/%m/%d %H\\\:%M\\\:%S}:box=1'",
            '-codec', 'h264',  # Hardware-accelerated; also tested AV1, Theora, FFV1, VP9, and VP8 but they were either too slow, had a larger output, or lower quality output
            '-preset', Config.output_compression,
            '-f', Config.output_format,
            '-force_key_frames', f'expr:gte(t,n_forced*{Config.output_keyframetime})',
            '-x264-params', 'rc_lookahead=1:sync_lookahead=1',  # two of the options from -tune=zerolatency
            '-t', str(remainingSeconds),
            'pipe:1'  # output to stdout
        ], stdout=subprocess.PIPE, stderr=stderr)

    # Set proc.stdout to non-blocking, meaning that .read() will just grab whatever is in the pipe and we can periodically (time-based) encrypt+write rather than waiting for more data
    fcntl.fcntl(proc.stdout.fileno(), fcntl.F_SETFL, os.O_NONBLOCK)

    with open(filename, 'ab') as outfile:  # Append in case the file already exists. Wouldn't want to overwrite the recording after evil haxxor replugs the pi...
        buf = b''
        while True:
            tmp = None
            try:
                tmp = proc.stdout.read()  # Raises exception instead of blocking
            except IOError:  # No data currently in the pipe
                pass

            if tmp is not None:
                buf += tmp

            if len(buf) > 0:
                outfile.write(ec.encrypt(buf))
                buf = b''

            if time.time() - starttime > Config.hours_per_recording * 3600 * 1.02:
                # ffmpeg process is running for 2% longer than it should have (e.g. 18 seconds on 3 hours).
                # Don't bother trying to terminate (SIGINT) first, output should be streamable anyhow and this
                # way we can just start a new ffmpeg and get on with a hopefully functional recording
                proc.kill()  
                break

            elif proc.poll() is not None:  # process has exited (it indeed should after -t seconds)
                break

            time.sleep(Config.encrypt_interval)

