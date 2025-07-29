# EncroCam: Open Source Security Camera

Streaming camera software with privacy and security in mind. Features:

- Authenticated video feed by digitally signing and timestamping the video (this protects against cut-and-paste attacks)
- Live streaming to any FTP drive: many storage vendor options, no lock-in or special server needed
- Runs on a Raspberry Pi with good video quality and decent compression size
- Encrypts for your existing PGP key (no new cryptographic keys to store, and the FTP vendor can't watch your feed)
- Decryption has seeking and streaming support for fast playback
- Detects when footage is missing on the server and uploads it automatically after an Internet outage
- Automatic recordings clean-up: no running out of space
- Integration with uptime monitor service to alert you when it is not recording

Learn [how to install](#install) or [how it works](#architecture).


<a name=install></a>
## Installation

The reference installation uses a Raspberry Pi 4 with a camera, running Ubuntu
Server 24.04 LTS for Raspberry Pi (ARM). EncroCam should also work out of the
box with Debian, Raspberry Pi OS, and probably most of the Debian family.

1. Install [Ubuntu Server](https://ubuntu.com/download/raspberry-pi) or a similar distribution
1. Log in to your device and install git if needed: `sudo apt install git`
1. Download the EncroCam code: `git clone https://github.com/x41sec/encrocam`
   - If you want to change where EncroCam looks for its files, move or rename the directory before installing
1. Run the installer: `cd encrocam && ./install`

The installer will install some dependencies with `apt`, ask you where to store
the recordings, whether to generate a fresh PGP signing key, etc. Afterwards,
it will tell you where to configure further steps like an uptime monitor and
SFTP server details, or you could run it as-is (storing data only locally).

The program cannot be run without setup: it needs to know which public key to
encrypt for, which private key to sign with... the private key should be stored
on a secure partition to make any sense, that secure partition needs
`cryptsetup` to be installed, and so on. The installer sets everything up and
asks your input where necessary.

An `./uninstall` script exists in the same directory.


<a name=requirements></a>
## Hardware requirements

- Camera: Any camera that shows up as `/dev/videoX` (any USB webcam, Pi Camera, etc.). You'll probably want one with built-in illumination (such as infrared) that turns on at night.
- CPU: Needs to be able to run `ffmpeg` with the desired compression level for your camera's video feed. The encrypting and uploading parts are relatively efficient, combined taking ~3% of one core on a Raspberry Pi 4.
- RAM: 512MB is recommended. About 70 MiB are needed for the Python processes but `ffmpeg` takes 225 MB in the reference setup; this is likely tunable, but consider the OS itself and you'll probably still want more than 256MB. Note that, during installation, `cryptsetup` will use [up to 50% of RAM](https://gitlab.com/cryptsetup/cryptsetup/-/blob/8be7b01ba83427fec638c0a84705ea5ea7634c62/lib/utils_pbkdf.c#L60) if there exists swap on your system, no matter what else you have running.
- Storage:
   - ~40MB for EncroCam, mainly for the encrypted partition of 32 MB. Log files are currently negligible (<100 KB before it gets rotated).
   - Video storage will depend greatly on your camera, the level of movement, and your settings. With the current defaults, our setup produces 285MB per hour of recording. This goes up to 490MB/hour during sunrise/-set when the light level is changing near constantly. This is fine for us because a few euros per month buys a terabyte of storage which translates to several months of footage retention.


<a name=architecture></a>
## Architecture

*As part of describing how the system is set up, this section describes
some attacks that we mitigate. For further attacks that we did not mitigate,
see the [attacks section](#attacks) below.*

The configuration lives on an encrypted LUKS partition that you unlock when
booting. This prevents an attacker from simply walking into the building,
yanking out the SD Card, and immediately having access to your signing key (to
forge recordings) or FTP credentials (to remove recordings). The recording can
still be wiped by your storage vendor, but they can't hide that an attack
happened because you'll notice your recordings are suddenly gone.

EncroCam calls `ffmpeg` with appropriate options for live streaming. The stream
is encrypted, signed, and written to a file in the local recordings directory,
where another process (listening for file write events using `inotify`) sees
the change and uploads the new data using the FTP `APPE` command ('append').
This way, an ordinary FTP drive can be used for streaming video, allowing you
to pick from many available file hosting vendors.

Old files are deleted automatically, both locally and remote: filenames contain
a time code, so it does not require a filesystem with accurate metadata, and you
can switch storage providers and copy over data without having to take care of
preserving file metadata.

The encryption uses an authenticated mode of AES. The key that decrypts and
authenticates the video data is called the *symmetric key*. The symmetric key
is encrypted for your regular PGP key and signed with another PGP key that is
specific to the EncroCam installation. The decrypter checks that the symmetric
key was signed by the installation's private key (note: you need to keep a copy
of this public key somewhere, in case an attacker destroyed or tampered with
the hardware), and then checks that the video data's authentication code is
valid.

Previously, all video frames would be encrypted with PGP which made encryption
and decryption as simple as calling a single command (per frame, but that's
easy enough to wrap in a loop): very robust, but the overhead here was also
substantial, both in terms of storage, encryption time, and decryption time.
It was decided that a custom but simple format would be better suited, allowing
more footage in less storage, better video quality because we're spending less
time on encryption, and much faster decryption. Part of the robustness is
maintained by having a magic string, long enough to be very unlikely to
randomly occur, which can be found even in corrupted/resumed files.
For details, see the EncroCrypt file format section.

By having cryptographically signed recordings with timestamps baked into the
video, you have reasonable confidence that the footage was recorded at this
time by this device. To replace (or even cut-and-paste) footage with a
different timestamp, an attacker would have to read your hardware's memory to
find the signing key.


## Technical documentation

The following subsections are meant for developers that want to understand or
make changes to the project.

### Overview of files

- `start.bash` starts `record.py` and `sync.py`. The latter is supposed to exit
  on a regular interval and `start.bash` will restart it.

- `record.py` is started once on startup (by `start.bash`) and records + encrypts. In more detail, it:
    1. launches `ffmpeg`,
    2. tells it to write data to stdout,
    3. collects that data non-blockingly for a configurable amount of time (like 1 second),
    4. passes it through EncroCrypt, and
    5. appends the encrypted data to the current file.

    After a configured time, it restarts `ffmpeg` and starts a new file.

- `sync.py` is restarted regularly and takes care of uploading and storage cleanup. Upon starting, it:
    1. removes old recordings from the local directory
    2. removes old recordings from the FTP drive
    3. checks that the remote end is complete, aside from the current file (in case Internet was out)
    4. listens with `inotify` and uploads files that are being modified (the bulk of the time should be spent here)
    5. after uploading data, if enough time passed since the last check-in, it checks in with the configured uptime
	   monitoring service (if the recording stops or upload fails, this code will not be triggered)
    6. exits after the configured time.

- `config_encrypted.py` contains:
    - Settings for the different scripts
    - Two filename functions because the filename contains a timestamp
	  which the recording and syncing scripts must both be able to read
      (if you change the filename format, you should also make sure it
	  can be converted back into a timestamp).

- `decrypter.py` is the script to decrypt a recording. It uses the settings in
  `config_encrypted.py` to know which PGP key should have signed the data. You
  should make a backup of the fingerprint in case the camera system is lost (or
  potentially tampered with) during a break-in.

- `EncroCrypt.py` contains the encryption and decryption code.
  The file format is detailed in the next section.

The recording and uploading systems are separate scripts such that they can
work independently. This prevents trouble with the recordings when the upload
was hanging, for example.

Filenames are in UTC to prevent (DST) issues, log output in local time (with
timezone indicated).


### EncroCrypt file format

The file contains the following items any number of times:

- Magic string `__EncroCrypt2`
- One byte packet type
- 4-byte unsigned integer length
- data

The packet types are currently either `\x01` and `\x02`, which are 'new key'
and 'video data' packets.

- The key packets contain only the encryption key as data. This symmetric
  encryption key is encrypted and signed with PGP.

- The video data packets contain: a 4-byte unsigned int timestamp, a 16-byte
  nonce, an N-byte ciphertext, and a 16-byte MAC. The ciphertext and
  corresponding MAC use the symmetric key from the most recent 'new key'
  packet. The algorithm used is AES GCM. For faster seeking, the timestamp is
  not encrypted or MAC'd. As noted in `decrypter.py --help`, the in-video
  timestamp is the verified one.


## Attacks
<a name=attacks></a>

Technical solutions, including EncroCam, are generally not as secure as hiring
guards 24/7. EncroCam is meant for people that just want a normal, open source
security camera with privacy in mind.

The [architecture section](#architecture) above discusses various protections
that are in place, such as against replay or cut-and-paste attacks. The
following list are known limitations of our solution and what actions you can
take against it.

1. Replacing the USB camera. The attacker would be on camera, and there would be a gap in the recording as you plug it
   around, but then they could feed it bogus video data. Attack is not tested; hypothetically `ffmpeg` would close,
   the Python managing script would notice and restart it, and after a short gap it would append video data to the
   recording.  
   - Mitigation 1: look for people appearing on the stream and then magically disappearing.  
   - Mitigation 2: make EncroCam detect a USB disconnect.

2. Dumping RAM to retrieve the private key, then forge video data and sign it with the obtained key, backdating for
   the amount of time you took doing this operation and overwriting the footage where you entered.  
   - Mitigation 1: have some enclosure for the Pi that requires removing power for a while before you can get to the actual hardware.  
   - Mitigation 2: implement a server which monitors upload interruptions and assumes a compromise if that happens. Even if the attacker tests and scripts everything perfectly, it should take a minute.  
   - Mitigation 3: implement a server which disallows removing or overwriting data, so you will see the person entering the room to do this attack.
     You also need a backup uplink and power if the attacker decides to cut Internet or power before entering.  

3. The attacker cuts power before entering the building, causing a gap in the recording
   but no footage that confirms compromise.  
   - Mitigation 1: assume a compromise if there is a gap in the recording.  
   - Mitigation 2: install a battery.

4. The attacker cuts Internet connectivity before entering the building.  
   - Mitigation 1: check the local recording files.  
   - Mitigation 2: backup Internet connection.  

These attacks seem fairly involved to us, not something a regular burglar will
do (you'd need to be a very valuable target).

The first weakness is acceptable to the authors because it is visible in the
recording. The other three require extra services for monitoring or hardware
that this software project cannot supply for you.


## Future work

While internally presenting the camera project, it came up that someone could
use this for doing journalistic work across borders. This is an interesting
use-case because you cannot watch back (and show law enforcement) what you
recorded until you get back home where your PGP private key is stored. However,
one should choose a server that does not allow deleting data once it is
uploaded. S3 nowadays supports appending to files (it did not when we started
this project), perhaps this also works together with object locking? Would be
something to look into!


## About

This project was developed by [X41 D-Sec GmbH](https://x41-dsec.de).
You are free to use it within the terms of the [license](./LICENSE).
Feel free to open a ticket or get in touch via other means if there
are any questions about the project!

