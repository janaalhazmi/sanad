#!/usr/bin/python3
"""
Minimal CBOR decoder + WebAuthn attestationObject/authenticatorData parser.

Why this exists: the previous registration flow asked the BROWSER to hand
back an already-parsed public key via `credential.response.getPublicKey()` /
`getPublicKeyAlgorithm()`. Those are the WebAuthn "Level 3" convenience
methods — NOT implemented consistently across real devices (notably many
iOS Safari versions, in-app webviews, and older Android WebView builds).
On any of those, `getPublicKey` is simply `undefined` and registration
fails immediately with a "browser doesn't support this" error — which is
almost certainly why "fingerprint authentication is not working" for real
users on real phones.

The fix: `attestationObject` itself (raw CBOR, containing `authData`) is
part of the ORIGINAL WebAuthn spec and has been supported by every
authenticator/browser since WebAuthn's introduction. We parse it here,
server-side, and extract the credential's public key ourselves — no
reliance on browser convenience APIs at all.

Only the (small) subset of CBOR that WebAuthn attestation objects actually
use is implemented: unsigned/negative integers, byte strings, text
strings, arrays, and maps (definite-length only — WebAuthn never emits
indefinite-length items or floats in these structures).
"""

import struct


class CBORDecodeError(Exception):
    pass


def _read_length(data, pos, additional_info):
    if additional_info < 24:
        return additional_info, pos
    if additional_info == 24:
        return data[pos], pos + 1
    if additional_info == 25:
        return struct.unpack(">H", data[pos:pos + 2])[0], pos + 2
    if additional_info == 26:
        return struct.unpack(">I", data[pos:pos + 4])[0], pos + 4
    if additional_info == 27:
        return struct.unpack(">Q", data[pos:pos + 8])[0], pos + 8
    raise CBORDecodeError(f"unsupported additional_info={additional_info} (indefinite-length CBOR not supported)")


def _decode(data, pos):
    if pos >= len(data):
        raise CBORDecodeError("unexpected end of CBOR data")
    initial_byte = data[pos]
    major_type = initial_byte >> 5
    additional_info = initial_byte & 0x1F
    pos += 1

    if major_type == 0:  # unsigned int
        val, pos = _read_length(data, pos, additional_info)
        return val, pos
    if major_type == 1:  # negative int
        val, pos = _read_length(data, pos, additional_info)
        return -1 - val, pos
    if major_type == 2:  # byte string
        length, pos = _read_length(data, pos, additional_info)
        return bytes(data[pos:pos + length]), pos + length
    if major_type == 3:  # text string
        length, pos = _read_length(data, pos, additional_info)
        return data[pos:pos + length].decode("utf-8"), pos + length
    if major_type == 4:  # array
        length, pos = _read_length(data, pos, additional_info)
        items = []
        for _ in range(length):
            item, pos = _decode(data, pos)
            items.append(item)
        return items, pos
    if major_type == 5:  # map
        length, pos = _read_length(data, pos, additional_info)
        result = {}
        for _ in range(length):
            key, pos = _decode(data, pos)
            value, pos = _decode(data, pos)
            result[key] = value
        return result, pos
    if major_type == 7:  # simple/float (only false/true/null/undefined needed here)
        if additional_info == 20:
            return False, pos
        if additional_info == 21:
            return True, pos
        if additional_info == 22:
            return None, pos
        raise CBORDecodeError(f"unsupported simple value additional_info={additional_info}")

    raise CBORDecodeError(f"unsupported CBOR major_type={major_type}")


def cbor_decode(data):
    """Decodes exactly one CBOR item from the start of `data` and returns it
    (ignores any trailing bytes, which attestationObject/authData don't have
    at the top level in the way we use this)."""
    value, _ = _decode(data, 0)
    return value


class AuthDataParseError(Exception):
    pass


def parse_authenticator_data(auth_data: bytes) -> dict:
    """Parses the binary authenticatorData structure:
    rpIdHash(32) + flags(1) + signCount(4) [+ attestedCredentialData] [+ extensions]

    attestedCredentialData (only present on registration, when flags bit 0x40
    is set): aaguid(16) + credentialIdLength(2) + credentialId(N) +
    credentialPublicKey (a CBOR-encoded COSE_Key map, variable length).

    Returns a dict with rp_id_hash, flags, sign_count, credential_id (bytes
    or None), credential_public_key (dict COSE key or None).
    """
    if len(auth_data) < 37:
        raise AuthDataParseError("authenticatorData too short")

    rp_id_hash = auth_data[:32]
    flags = auth_data[32]
    sign_count = struct.unpack(">I", auth_data[33:37])[0]

    pos = 37
    credential_id = None
    credential_public_key = None

    attested_credential_data_present = bool(flags & 0x40)
    if attested_credential_data_present:
        if len(auth_data) < pos + 18:
            raise AuthDataParseError("truncated attestedCredentialData")
        pos += 16  # aaguid, unused here
        cred_id_len = struct.unpack(">H", auth_data[pos:pos + 2])[0]
        pos += 2
        credential_id = auth_data[pos:pos + cred_id_len]
        pos += cred_id_len
        # The COSE key is a CBOR map; we don't know its byte length up
        # front, so decode it via the generic decoder starting at `pos`
        # and let it tell us where it ended.
        cose_key, new_pos = _decode(auth_data, pos)
        credential_public_key = cose_key
        pos = new_pos

    return {
        "rp_id_hash": rp_id_hash,
        "flags": flags,
        "sign_count": sign_count,
        "credential_id": credential_id,
        "credential_public_key": credential_public_key,
        "raw": auth_data,
    }


def parse_attestation_object(attestation_object: bytes) -> dict:
    """Top-level entry point: CBOR-decodes the attestationObject and parses
    its embedded authData. Returns {fmt, auth_data (bytes), parsed_auth_data
    (dict, see parse_authenticator_data)}."""
    decoded = cbor_decode(attestation_object)
    if not isinstance(decoded, dict) or "authData" not in decoded:
        raise AuthDataParseError("attestationObject missing authData")
    auth_data_bytes = decoded["authData"]
    return {
        "fmt": decoded.get("fmt"),
        "auth_data": auth_data_bytes,
        "parsed_auth_data": parse_authenticator_data(auth_data_bytes),
    }


# COSE key map indices we care about (see RFC 9053 / WebAuthn spec).
COSE_KTY = 1
COSE_ALG = 3
COSE_EC2_CRV = -1
COSE_EC2_X = -2
COSE_EC2_Y = -3
COSE_RSA_N = -1
COSE_RSA_E = -2

COSE_KTY_EC2 = 2
COSE_KTY_RSA = 3
COSE_CRV_P256 = 1


def cose_key_to_der_public_key(cose_key: dict) -> tuple:
    """Converts a decoded COSE_Key map into a DER-encoded SubjectPublicKeyInfo
    (the same format the rest of app_server.py already stores/verifies
    against via `serialization.load_der_public_key`). Returns
    (der_bytes, alg:int).

    Only ES256 (P-256 EC) and RS256 (RSA) are supported, matching the
    pubKeyCredParams we actually request during registration."""
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    from cryptography.hazmat.primitives import serialization

    kty = cose_key.get(COSE_KTY)
    alg = cose_key.get(COSE_ALG)

    if kty == COSE_KTY_EC2:
        crv = cose_key.get(COSE_EC2_CRV)
        x = cose_key.get(COSE_EC2_X)
        y = cose_key.get(COSE_EC2_Y)
        if crv != COSE_CRV_P256 or not isinstance(x, bytes) or not isinstance(y, bytes):
            raise AuthDataParseError("unsupported or malformed EC2 COSE key (only P-256 supported)")
        x_int = int.from_bytes(x, "big")
        y_int = int.from_bytes(y, "big")
        public_numbers = ec.EllipticCurvePublicNumbers(x_int, y_int, ec.SECP256R1())
        public_key = public_numbers.public_key()
        der = public_key.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return der, -7  # ES256

    if kty == COSE_KTY_RSA:
        n = cose_key.get(COSE_RSA_N)
        e = cose_key.get(COSE_RSA_E)
        if not isinstance(n, bytes) or not isinstance(e, bytes):
            raise AuthDataParseError("malformed RSA COSE key")
        n_int = int.from_bytes(n, "big")
        e_int = int.from_bytes(e, "big")
        public_numbers = rsa.RSAPublicNumbers(e_int, n_int)
        public_key = public_numbers.public_key()
        der = public_key.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return der, -257  # RS256

    raise AuthDataParseError(f"unsupported COSE key type kty={kty}")
