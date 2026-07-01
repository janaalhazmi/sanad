// WebAuthn helpers — real platform biometric auth (Face ID / Touch ID / Windows Hello /
// Android fingerprint), backed by the browser's Secure Enclave / Keystore / TPM.
// The private key never leaves the device; only a public key is sent to the server.

function isBiometricSupported() {
  return !!(window.PublicKeyCredential && navigator.credentials);
}

function b64urlToBuffer(b64url) {
  let str = b64url.replace(/-/g, "+").replace(/_/g, "/");
  while (str.length % 4) str += "=";
  const bin = atob(str);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

function bufferToB64url(buf) {
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function bufferToB64(buf) {
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}

async function checkPlatformAuthenticatorAvailable() {
  if (!isBiometricSupported()) return false;
  try {
    return await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable();
  } catch (e) {
    return false;
  }
}

/**
 * Enroll this device's biometrics (Face ID / Touch ID / Windows Hello / fingerprint).
 * Must be called while the user is already logged in (e.g. from Settings).
 */
async function registerBiometric() {
  if (!isBiometricSupported()) {
    return { success: false, message: "المتصفح لا يدعم تسجيل الدخول بالبصمة" };
  }

  const { data: options, ok } = await apiFetch("/api/webauthn/register/options", { method: "POST", body: {} });
  if (!ok) return { success: false, message: options.message || "تعذر بدء عملية التسجيل" };

  const publicKey = {
    ...options,
    challenge: b64urlToBuffer(options.challenge),
    user: { ...options.user, id: b64urlToBuffer(options.user.id) },
  };

  let credential;
  try {
    credential = await navigator.credentials.create({ publicKey });
  } catch (e) {
    return { success: false, message: "تم إلغاء أو فشل التحقق بالبصمة" };
  }

  if (!credential.response.getPublicKey) {
    return { success: false, message: "المتصفح لا يدعم استخراج المفتاح العام لهذا النوع من البصمة" };
  }

  const payload = {
    id: credential.id,
    clientDataJSON: bufferToB64url(credential.response.clientDataJSON),
    publicKey: bufferToB64(credential.response.getPublicKey()),
    alg: credential.response.getPublicKeyAlgorithm(),
  };

  const { data, ok: verifyOk } = await apiFetch("/api/webauthn/register/verify", { method: "POST", body: payload });
  if (verifyOk && data.success) {
    return { success: true, message: data.message || "تم تفعيل الدخول بالبصمة" };
  }
  return { success: false, message: data.message || "تعذر تفعيل الدخول بالبصمة" };
}

/**
 * Log in using this device's already-enrolled biometrics.
 */
async function loginWithBiometric() {
  if (!isBiometricSupported()) {
    return { success: false, message: "المتصفح لا يدعم تسجيل الدخول بالبصمة" };
  }

  const { data: options, ok, status } = await apiFetch("/api/webauthn/login/options", { method: "POST", body: {} });
  if (!ok) {
    if (status === 404) return { success: false, message: "الدخول بالبصمة غير مفعّل على هذا الحساب" };
    return { success: false, message: options.message || "تعذر بدء عملية التحقق" };
  }

  const publicKey = {
    ...options,
    challenge: b64urlToBuffer(options.challenge),
    allowCredentials: (options.allowCredentials || []).map((c) => ({ ...c, id: b64urlToBuffer(c.id) })),
  };

  let assertion;
  try {
    assertion = await navigator.credentials.get({ publicKey });
  } catch (e) {
    return { success: false, message: "تعذر التحقق بالبصمة، جرّب كلمة المرور" };
  }

  const payload = {
    id: assertion.id,
    clientDataJSON: bufferToB64url(assertion.response.clientDataJSON),
    authenticatorData: bufferToB64url(assertion.response.authenticatorData),
    signature: bufferToB64url(assertion.response.signature),
  };

  const { data, ok: verifyOk } = await apiFetch("/api/webauthn/login/verify", { method: "POST", body: payload });
  if (verifyOk && data.success) {
    return { success: true, redirect: data.redirect || "/dashboard" };
  }
  return { success: false, message: data.message || "فشل التحقق بالبصمة" };
}

/**
 * Verify the currently pending sensitive action (transfer / add beneficiary)
 * using this device's biometrics. Real WebAuthn assertion — never assumed
 * to succeed; the server independently verifies the cryptographic
 * signature before executing anything.
 */
async function verifyActionWithBiometric() {
  if (!isBiometricSupported()) {
    return { success: false, message: "المتصفح لا يدعم التحقق بالبصمة" };
  }

  const { data: options, ok, status } = await apiFetch("/api/action/webauthn/options", { method: "POST", body: {} });
  if (!ok) {
    if (status === 404) return { success: false, message: options.message || "الدخول بالبصمة غير مفعّل" };
    return { success: false, message: options.message || "تعذر بدء عملية التحقق" };
  }

  const publicKey = {
    ...options,
    challenge: b64urlToBuffer(options.challenge),
    allowCredentials: (options.allowCredentials || []).map((c) => ({ ...c, id: b64urlToBuffer(c.id) })),
  };

  let assertion;
  try {
    assertion = await navigator.credentials.get({ publicKey });
  } catch (e) {
    return { success: false, message: "تم إلغاء أو فشل التحقق بالبصمة" };
  }

  const payload = {
    id: assertion.id,
    clientDataJSON: bufferToB64url(assertion.response.clientDataJSON),
    authenticatorData: bufferToB64url(assertion.response.authenticatorData),
    signature: bufferToB64url(assertion.response.signature),
  };

  const { data, ok: verifyOk } = await apiFetch("/api/action/webauthn/verify", { method: "POST", body: payload });
  if (verifyOk && data.success) {
    return { success: true, message: data.message, redirect: data.redirect, balance: data.balance };
  }
  return { success: false, message: data.message || "فشل التحقق بالبصمة" };
}
