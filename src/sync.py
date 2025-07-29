#!/usr/bin/env python3

# Stdlib
import sys, os, time, ftplib, ssl
# Third-party dependencies
import pyinotify, requests
# Local imports
sys.path.append('../')
from encrypted_mountpoint.config import *

def shouldRemove(filename):
    try:
        t = filenameToTime(filename)
    except:
        return True if Config.remove_unrecognized_files else False

    if t > time.time() and Config.remove_unrecognized_files:  # parsing failed (file from the future?!), we should get rid of it as well
        return True

    delete_if_before = time.time() - (Config.keep_history_days * 24 * 3600)

    if t < delete_if_before:  # Even if remove_unrecognized_files is not set, we should still remove old files
        return True

    return False


def tprint(msg, printfunc=print):
    msgwithtime = time.strftime('%a %d %b %H:%M:%S %z', time.localtime()) + ' ' + msg
    printfunc(msgwithtime)

    if Config.logfile_dir != False:
        fname = 'stdout' if printfunc == print else 'stderr'
        with open(f'{Config.logfile_dir}/{time.strftime("%A")}-{fname}.log', 'at', encoding='UTF-8') as f:
            f.write(msgwithtime + ('\n' if printfunc == print else ''))


def MLSD(ftps):
    raw = ftps.mlsd()
    dirlist_dict = {}
    for f in raw:
        # dirlist_dict[filename] = {attribute: value, ...}
        dirlist_dict[f[0]] = f[1]

    return dirlist_dict


class NotifyHandler(pyinotify.ProcessEvent):
    def process_IN_MODIFY(self, event):
        return self.doStuff(event)


    def process_IN_CLOSE_WRITE(self, event):
        return self.doStuff(event)


    def doStuff(self, event):
        global last_monitoring, remote_dirlist  # we'll update these after appending to a file

        if shouldRemove(event.name):  # in case garbage is showing up, let's not blindly upload that
            tprint(f'Warning: garbage file "{event.name}" being written to in the local directory. Refusing to upload.\n', sys.stderr.write)
            return

        if not disable_uploading:
            with open(f'{local_dir}/{event.name}', 'rb') as fp:
                cmd = 'STOR'  # upload new file
                if event.name in remote_dirlist:
                    fp.seek(int(remote_dirlist[event.name]['size']))
                    cmd = 'APPE'  # append

                ftps.storbinary(f'{cmd} {event.name}', fp)
                remote_dirlist = MLSD(ftps)

        if Config.monitoring_url.strip() not in [None, ''] and last_monitoring + Config.monitoring_interval < time.time():
            requests.get(Config.monitoring_url, timeout=3)
            last_monitoring = time.time()

        if time.time() > starttime + sync_restart_after_seconds:
            tprint('Sync: time up, restarting')
            sys.exit(0)


encrocam_homedir = sys.argv[1]
if '__encrocam_homedir__' in Config.logfile_dir:
    Config.logfile_dir = Config.logfile_dir.replace('__encrocam_homedir__', encrocam_homedir)
if Config.logfile_dir != False and not os.path.isdir(Config.logfile_dir):
    try:
        os.mkdir(Config.logfile_dir)
        tprint('Created log directory')
    except:
        Config.logfile_dir = False
        tprint('Failed to create log directory:', Config.logfile_dir)

starttime = time.time()
last_monitoring = -1
local_dir = sys.argv[2]
sync_restart_after_seconds = int(sys.argv[3]) * 60

tprint('Checking for local garbage')
local_dirlist = os.listdir(local_dir)  # will not contain . and .. according to python docs
for f in local_dirlist:
    if shouldRemove(f):
        tprint(f'...removing local {f}')
        os.remove(f'{local_dir}/{f}')

local_dirlist = os.listdir(local_dir)  # refresh after potentially having deleted files (we use it later)

disable_uploading = False
if ftp_host is None or len(ftp_host) == 0:
    disable_uploading = True
    tprint('Skipped remote operations because ftp_host is empty or None')
else:
    context = ssl.create_default_context()
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True  # Seems to be implicit for CERT_REQUIRED but...
    context.minimum_version = ssl.TLSVersion.TLSv1_3

    ftps = ftplib.FTP_TLS(Config.ftp_host, context=context, timeout=Config.ftp_timeout)
    ftps.login(Config.ftp_user, Config.ftp_pass)
    ftps.prot_p()  # Require data connection to be secure (not just control connection)

    ftps.cwd(Config.ftp_dir)

    tprint('Checking for remote garbage')
    remote_dirlist = MLSD(ftps)
    for fname in remote_dirlist:
        if fname not in ['.', '..'] and shouldRemove(fname):
            tprint(f'...removing remote {fname}')
            ftps.delete(fname)

    tprint('Checking if remote has any missing or incomplete files')
    for fname in local_dirlist:
        path = f'{local_dir}/{fname}'
        # ignore if it's the current recording, we'll get to that, this is only about past recordings that weren't uploaded (completely)
        if fname != timeToFilename(time.time()):
            if fname not in remote_dirlist:
                tprint(f'Uploading missing file {fname}')
                with open(path, 'rb') as fp:
                    ftps.storbinary(f'STOR {fname}', fp)
            elif remote_dirlist[fname]['type'] != 'file':
                continue
            elif os.path.getsize(path) != int(remote_dirlist[fname]['size']):
                tprint(f'Appending to incomplete file {fname}')
                with open(path, 'rb') as fp:
                    fp.seek(int(remote_dirlist[fname]['size']))
                    ftps.storbinary(f'APPE {fname}', fp)  # append

if time.time() > starttime + (sync_restart_after_seconds / 2):
    tprint("Warning: used up more than half the time for maintenance! Should either check what's up or increase sync_restart_after\n", sys.stderr.write)

tprint('Starting inotify listener')
wm = pyinotify.WatchManager()
notifier = pyinotify.Notifier(wm, NotifyHandler())
wm.add_watch(local_dir, pyinotify.IN_MODIFY | pyinotify.IN_CLOSE_WRITE)  # modify events and closed-file-that-was-open-for-writing events
notifier.loop()

