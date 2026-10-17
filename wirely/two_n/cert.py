import sys
import base64

import cryptography.x509
import cryptography.hazmat.backends
import cryptography.hazmat.primitives.serialization
import cryptography.hazmat.primitives.hashes


def check_certificate_fingerprint(conn, sslsocket, tlsa, require_all=False):
    server_certificate = sslsocket.getpeercert(True)
    if server_certificate is None:
        raise Exception('No certificate in use')

    pubkey = None
    matches = 0
    mismatches = 0
    for assoc in tlsa:
        # https://www.rfc-editor.org/rfc/rfc6698.html#section-2.1

        fields = assoc.split(' ')
        if len(fields) != 4:
            print('Warning: Got TLSA entry with %d fields' % (len(fields),), file=sys.stderr, flush=True)
            continue
        usage = int(fields[0])
        selector = int(fields[1])
        matching = int(fields[2])
        expectedData = base64.b16decode(fields[3].upper())

        # https://www.rfc-editor.org/rfc/rfc6698.html#section-2.1.1
        if usage != 3:
            print('Warning: Got TLSA entry with usage %d' % (usage,), file=sys.stderr, flush=True)
            continue

        # https://www.rfc-editor.org/rfc/rfc6698.html#section-2.1.2
        if selector == 0:
            actual = server_certificate
        elif selector == 1:
            if pubkey is None:
                cert = cryptography.x509.load_der_x509_certificate(server_certificate, cryptography.hazmat.backends.default_backend())
                pubkey = cert.public_key().public_bytes(cryptography.hazmat.primitives.serialization.Encoding.DER, cryptography.hazmat.primitives.serialization.PublicFormat.SubjectPublicKeyInfo)
            actual = pubkey
        else:
            # print('Warning: Got TLSA entry with selector %d' % (selector,), file=sys.stderr, flush=True)
            continue

        # https://www.rfc-editor.org/rfc/rfc6698.html#section-2.1.3
        if matching == 0:
            actualHash = actual
        else:
            if matching == 1:
                hash: cryptography.hazmat.primitives.hashes.HashAlgorithm = cryptography.hazmat.primitives.hashes.SHA256()
            elif matching == 2:
                hash = cryptography.hazmat.primitives.hashes.SHA512()
            else:
                # print('Warning: Got TLSA entry with matching type %d' % (matching,), file=sys.stderr, flush=True)
                continue
            digest = cryptography.hazmat.primitives.hashes.Hash(hash, cryptography.hazmat.backends.default_backend())
            digest.update(actual)
            actualHash = digest.finalize()

        if expectedData == actualHash:
            matches += 1
        else:
            mismatches += 1

        # print('Got %d %d %d => %d' % (usage, selector, matching, expectedData == actualHash), file=sys.stderr, flush=True)
            
    # print('Got %d matches, %d mismatches for %d entries' % (matches, mismatches, len(tlsa)), file=sys.stderr, flush=True)
    if matches < 1:
        raise Exception('Unable to verify server certificate')
    elif require_all and matches != len(tlsa):
        raise Exception('Could not verify all entries: Got %d matches, %d mismatches for %d entries' % (matches, mismatches, len(tlsa)))
    else:
        if conn is not None:
            conn.cert_is_checked = sslsocket


def build_certificate_fingerprint(server_certificate, usage=3, selector=1, matching=2):
    cert = cryptography.x509.load_der_x509_certificate(server_certificate, cryptography.hazmat.backends.default_backend())
    pubkey = cert.public_key().public_bytes(cryptography.hazmat.primitives.serialization.Encoding.DER, cryptography.hazmat.primitives.serialization.PublicFormat.SubjectPublicKeyInfo)

    # https://www.rfc-editor.org/rfc/rfc6698.html#section-2.1.1
    if usage != 3:
        raise Exception('TODO: usage != 3')

    # https://www.rfc-editor.org/rfc/rfc6698.html#section-2.1.2
    if selector == 0:
        actual = server_certificate
    elif selector == 1:
        actual = pubkey
    else:
        raise Exception('Unknown selector {}'.format(selector))

    # https://www.rfc-editor.org/rfc/rfc6698.html#section-2.1.3
    if matching == 0:
        actualHash = actual
    else:
        if matching == 1:
            hash: cryptography.hazmat.primitives.hashes.HashAlgorithm = cryptography.hazmat.primitives.hashes.SHA256()
        elif matching == 2:
            hash = cryptography.hazmat.primitives.hashes.SHA512()
        else:
            raise Exception('Unknown matching {}'.format(matching))
        digest = cryptography.hazmat.primitives.hashes.Hash(hash, cryptography.hazmat.backends.default_backend())
        digest.update(actual)
        actualHash = digest.finalize()

    return '{} {} {} {}'.format(usage, selector, matching, str(base64.b16encode(actualHash), 'utf-8'))


def main():
    if len(sys.argv) != 2:
        print('Usage: python3 -m wirely.two_n.cert <cert.crt>')
        sys.exit(1)
    filename = sys.argv[1]

    with open(filename, 'rb') as f:
        data = f.read()

    crt = cryptography.x509.load_pem_x509_certificate(data, cryptography.hazmat.backends.default_backend())

    print(build_certificate_fingerprint(crt.public_bytes(cryptography.hazmat.primitives.serialization.Encoding.DER)))


if __name__ == '__main__':
    main()
