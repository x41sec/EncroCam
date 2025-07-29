
class Config:
    # settings for sync.py
    ftp_host = ''  # FTP server (hostname or FQDN) to connect to. Use empty string or None to not livestream the encrypted recording data.
    ftp_user = ''  # Username to log into the FTP server
    ftp_pass = ''  # Password to log into the FTP server
    ftp_dir = '/'  # Remote directory on the FTP server. This directory must exist and should ideally be dedicated for EncroCam so you can set remove_unrecognized_files to True
    ftp_timeout = 15  # seconds. Avoid indefinite network hangs. The timer seems to reset frequently (like with every network packet), so a few RTTs should be enough (a handful of seconds)
    remove_unrecognized_files = True  # Remove any files (local and remote) whose name does not parse with filenameToTime()
    keep_history_days = 7  # Automatically remove files (local and remote) that are older than...
    monitoring_url = ''  # URL to call to indicate to your uptime monitoring service that we're still online. Use empty string or None to turn off.
    monitoring_interval = 60 * 29  # seconds interval between calling the service (may be delayed a few seconds depending on if it's busy uploading recording data)
    logfile_dir = '__encrocam_homedir__/logs/'  # Where to write log files. The special value __encrocam_homedir__ gets replaced with the directory above where this configuration file is. Set to False (without quotes) to disable logging to file. Bit hacky, TODO we should probably use /var/log.

    # settings for record.py
    hours_per_recording = 24  # Restart ffmpeg and start a new file every N hours, so you don't have to download many gigabytes of recording at once, and to prevent any ffmpeg memory leaks from messing things up (quick test in 2020: across 2.5h, it leaked about 6MB). Restarting leaves a ~4-second gap in the recording, so don't do this too often either
    input_device = '/dev/video0'
    input_format = 'v4l2'
    input_framerate = 30  # See v4l2-ctl --list-formats-ext for supported resolution+fps combinations
    input_resolution = '1280x720'
    output_format = 'hls'  # HLS (HTTP Live Streaming) allows taking arbitrary cuts of the output file
    output_compression = 'faster'  # Tested to be very well within cpu tolerance of the Pi so it won't lag behind, and quite close to good compression
    output_keyframetime = 5  # Every how many seconds should a keyframe be written? Values <5 seem to result in significantly larger files. A lower value is useful for decrypting, since we can skip decrypting a section and it will result in only value/2 seconds of black screen on average. It does *not* mean we'll have gaps in the recording.
    encrypt_interval = 1/8  # seconds to collect data from ffmpeg's stdout before encrypting it and writing it to a file. Shorter means more 'live' streaming, but also slightly more storage overhead

    # If you are looking for the recipients or signing fingerprint configuration
    # which was previously here, this moved to the unencrypted configuration.
    # They aren't secret, and now the installer can help configure those values.


# The following functions are not part of the configuration

def timeToFilename(time):  # unix timestamp
    slot = int(time / 3600 / Config.hours_per_recording)
    return f'rec-{slot}.encrocam'


def filenameToTime(filename):
    # filename may include path
    if '/' in filename:
        filename = filename.split('/')[-1]

    slot = filename.split('.')[0].split('-')[1]  # '/path/to/rec-123.encrocam' -> '123'
    return Config.hours_per_recording * 3600 * int(slot)

