#!/usr/bin/env python3

import sys, os, struct, time  # stdlib imports
import gnupg
from Cryptodome.Cipher import AES

def statusinfo(message):
    sys.stdout.write(message + '\r')


def warn(message):
    sys.stderr.write(message + '\n')


def timefmt(timestamp):
    return time.strftime("%a %H:%M", time.localtime(timestamp))


class EncroCrypt:
    MAGIC = b'__EncroCrypt2'  # Appears in front of every packet, long enough not to randomly occur in encrypted data before the Sun burns out

    LENGTH_ENCRYPTION_KEY = 16
    LENGTH_NONCE          = 16
    LENGTH_MAC            = 16
    MAX_GCM_INVOCATIONS   = int(2**32)  # per NIST SP 800-38d, page 21, the paragraph in bold text

    PACKET_NEWKEY    = b'\x01'
    PACKET_VIDEODATA = b'\x02'

    PACKET_MAXLENGTH = 1024 * 1024 * 10

    struct_int = struct.Struct(">I")

    def __init__(self, signing_fingerprint, encrypt_fingerprint=None, gnupghome=None):
        """
        encrocrypt_obj = EncroCrypt(string, string or None, string or None)
        encrypt_fingerprint is only required when encrypting (fingerprint of the key used to encrypt)
        """
        self.gpg = gnupg.GPG(gnupghome=gnupghome)
        self.encrypt_fingerprint = encrypt_fingerprint
        self.signing_fingerprint = signing_fingerprint
        self.key = None
        self.showed_data_before_key_warning = False


    def _pack(self, packet_type, data):
        return EncroCrypt.MAGIC + packet_type + EncroCrypt.struct_int.pack(len(data)) + data


    def _new_symmetric_key(self):
        self.key = os.urandom(EncroCrypt.LENGTH_ENCRYPTION_KEY)
        self.gcm_invocations_with_same_key = 0

        # If signing_fingerprint is not found or invalid, GnuPG will use another available secret key. The python bindings don't have a way to force using a certain fingerprint.
        # This should not be an issue for us because the GnuPG homedir is in EncroCam's encrypted partition, so it should contain no other secret keys.
        result = self.gpg.encrypt(data=self.key, recipients=[self.encrypt_fingerprint], sign=self.signing_fingerprint, armor=False)
        if not result.ok:
            raise Exception('Encryption failed. Is the GnuPG home directory set correctly, the key fingerprints configured, and the encryption key verified/signed?')

        return self._pack(EncroCrypt.PACKET_NEWKEY, result.data)


    def encrypt(self, data):
        """
        encrocrypt_obj.encrypt(bytes object)
        Returns the ciphertext as bytes object; potentially preceded by a new key packet if a new encryption key is needed.
        """

        output = b''

        if self.key is None:
            output += self._new_symmetric_key()

        while len(data) > 0:
            if self.gcm_invocations_with_same_key > EncroCrypt.MAX_GCM_INVOCATIONS:
                output += self._new_symmetric_key()

            timestamp = EncroCrypt.struct_int.pack(int(time.time() / 60))
            nonce = os.urandom(EncroCrypt.LENGTH_NONCE)
            # Newly configure the cipher every time because we want message authentication on each small
            # part instead of having a cut-off file with missing authentication on the last few minutes.
            # A rolling MAC would be better, so that we can add the updated tag without having to also
            # generate and add a new nonce every time, and it might be better to do nonce=nonce+1%maxuint
            # instead of reading urandom all day long, but pycryptodome tells you to do it this way and
            # NIST says it's fine for >=96 bits as well, so to avoid needing to validate this idea we've
            # gone with the default recommendation. It does still seem like the better idea though, also
            # based on <https://words.filippo.io/dispatches/xaes-256-gcm-11/>. Future work...
            cipher = AES.new(mode=AES.MODE_GCM, key=self.key, nonce=nonce)
            ciphertext, mac = cipher.encrypt_and_digest(data[ : EncroCrypt.PACKET_MAXLENGTH])
            self.gcm_invocations_with_same_key += 1
            output += self._pack(EncroCrypt.PACKET_VIDEODATA, timestamp + nonce + ciphertext + mac)

            data = data[EncroCrypt.PACKET_MAXLENGTH : ]

        return output


    def _seek_to_magic(self):
        buf = b''
        while True:
            tmp = self.streamed_read(1)
            if len(tmp) == 0:
                return False

            buf += tmp

            if buf[-len(EncroCrypt.MAGIC) : ] == EncroCrypt.MAGIC:
                warn(f'Found a magic token at {self.streamreader_source.tell()}')
                return True

            if len(buf) > len(EncroCrypt.MAGIC) * 50:
                buf = buf[-len(EncroCrypt.MAGIC) : ]


    def stream_reader(self, stream):
        # This allows us to prepend data if we read too far. Avoids depending on seekable input; this way you can use stdin.
        self.streamreader_source = stream
        self.streamreader_buffer = b''


    def streamed_read(self, length):
        # Read from the data stream created using self.stream_reader(fd)
        if len(self.streamreader_buffer) < length:
            val = self.streamreader_buffer + self.streamreader_source.read(length - len(self.streamreader_buffer))
            self.streamreader_buffer = b''
        else:
            val = self.streamreader_buffer[0 : length]
            self.streamreader_buffer = self.streamreader_buffer[length : ]

        return val


    def decrypt(self, encrypted_stream, decrypted_stream, skip_until=None):
        """
        encrocrypt_obj.decrypt(file object, file object, int or None)
        Reads EncroCrypt-formatted bytes from the first argument and writes the plaintext to the second argument,
        seeking in the input until finding the right integer in a video data packet if skip_until is not None.
        Will write to stderr for non-fatal issues.
        """
        self.stream_reader(encrypted_stream)

        while True:
            val = self.streamed_read(len(EncroCrypt.MAGIC))
            if len(val) == 0:  # EOF
                return True

            if val != EncroCrypt.MAGIC:
                wasat = encrypted_stream.tell()
                if not self._seek_to_magic():
                    raise Exception(f'File cut off, no valid data found since around {wasat} bytes (reason: missing magic)')

            packet_type = self.streamed_read(1)
            if len(packet_type) == 0:
                warn(f'File cut off at byte offset {encrypted_stream.tell()} (reason: missing packet type)')
                return False

            try:
                packet_length = EncroCrypt.struct_int.unpack(self.streamed_read(4))[0]
                if packet_length > EncroCrypt.PACKET_MAXLENGTH:
                    # We stumbled upon some random data... seek the next magic token
                    warn(f'Indicated packet length impossibly long at byte offset {encrypted_stream.tell()}, skipping to the next magic token')
                    continue

                if packet_length == 0:
                    warn(f'Zero-length data of type {packet_type} at offset {encrypted_stream.tell()} in encrypted stream')
                    continue

                packet_data = self.streamed_read(packet_length)

                if len(packet_data) != packet_length:
                    # If you keep in mind that it's unauthenticated, you could try if part of this packet is decryptable and squeeze
                    # a few more frames out of the encrypted video file (i.e. don't return false and skip some validation below).
                    # Not supported by default to avoid giving a false sense of reliability (an attacker could use this). If something
                    # important happened, someone knowledgeable can look into the source and make their own educated decisions rather
                    # than getting unauth'd data without realizing.
                    warn(f'File cut off at byte offset {encrypted_stream.tell()} (reason: incomplete read)')
                    return False

                if EncroCrypt.MAGIC in packet_data:
                    # partial packet... rewind to magic and retry
                    # (Same as above: you might be able to recover something here if you keep in mind it's unauthenticated.)
                    self.streamreader_buffer = packet_data[packet_data.index(EncroCrypt.MAGIC) : ] + self.streamreader_buffer
                    continue

                if packet_type == EncroCrypt.PACKET_NEWKEY:
                    decrypted = self.gpg.decrypt(packet_data)
                    if not decrypted.ok:
                        raise Exception('Failed to decrypt PGP data')

                    if decrypted.fingerprint != self.signing_fingerprint:
                        raise Exception('Signature not from a trusted key: signed with fingerprint "{}", should be "{}"'.format(decrypted.fingerprint, self.signing_fingerprint))
                    else:
                        self.key = decrypted.data

                elif packet_type == EncroCrypt.PACKET_VIDEODATA:
                    timestamp = EncroCrypt.struct_int.unpack(packet_data[ : 4])[0] * 60

                    if self.key is None:
                        if not self.showed_data_before_key_warning:
                            warn(f'Found a video data packet (timestamped {timefmt(timestamp)}) before having seen an encryption key packet: cannot decrypt this.')
                            self.showed_data_before_key_warning = True
                        continue
                    else:
                        if self.showed_data_before_key_warning:
                            warn(f'Found a video data packet (timestamped {timefmt(timestamp)}) for which we do have the key.')
                        self.showed_data_before_key_warning = False

                    if skip_until is not None and timestamp < skip_until:
                        statusinfo(f'Seeking... ({timestamp}/{skip_until})')
                        continue

                    nonce = packet_data[4 : 4 + EncroCrypt.LENGTH_NONCE]
                    ciphertext = packet_data[4 + EncroCrypt.LENGTH_NONCE : -EncroCrypt.LENGTH_MAC]
                    mac = packet_data[-EncroCrypt.LENGTH_MAC : ]

                    cipher = AES.new(mode=AES.MODE_GCM, key=self.key, nonce=nonce)

                    try:
                        decrypted = cipher.decrypt_and_verify(ciphertext, mac)
                        if nonce[-1] < 8:  # update once every 8/256 decrypts on average
                            statusinfo(f'Decrypted video data with verified signature until {timefmt(timestamp)}...')
                    except ValueError:
                        warn(f'MAC validation failed at byte offset {encrypted_stream.tell()}. Bit rot, or has the file been tampered with?')
                        continue

                    decrypted_stream.write(decrypted)

                else:
                    raise Exception('Invalid packet type: data corrupted or made with a newer version')

            except Exception as e:
                warn(f'{type(e).__name__} in {e.__traceback__.tb_frame.f_code.co_filename}:{e.__traceback__.tb_lineno} | offset in encrypted stream: {encrypted_stream.tell()} | error message: {e}')
                # TODO check if this is useful
                """
                if 'Signature not from a trusted key' in str(e):
                    warn('To resolve key trust issues, use `gpg --lsign-key FINGERPRINT`')
                """
                continue

