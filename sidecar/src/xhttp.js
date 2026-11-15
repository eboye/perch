'use strict';
// Signed requests to X endpoints that rettiwt-api does not wrap (Spaces stream, DM send).

const { AuthCredential } = require('rettiwt-api/dist/models/auth/AuthCredential');
const { AuthService } = require('rettiwt-api/dist/services/internal/AuthService');
const { Cookie } = require('cookiejar');

// X answers 404 to non-browser user agents on several of these endpoints.
const BROWSER_UA = 'Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0';
// The web client sends a per-session client UUID with DM requests (mautrix-twitter does the same).
const CLIENT_UUID = require('node:crypto').randomUUID();

function authHeaders(client) {
	const cookies = AuthService.decodeCookie(client.apiKey)
		.split(';')
		.map((item) => new Cookie(item));
	return {
		...new AuthCredential(cookies).toHeader().toJSON(),
		'x-twitter-active-user': 'yes',
		'x-twitter-auth-type': 'OAuth2Session',
		'x-twitter-client-language': 'en',
		referer: 'https://x.com/',
		'user-agent': BROWSER_UA,
		'x-client-uuid': CLIENT_UUID,
	};
}

/** fetch() with the session's auth headers plus a client transaction id (rettiwt's generator, TS-private). */
async function signedFetch(client, url, init = {}) {
	const method = (init.method || 'GET').toUpperCase();
	const transaction = await client.space._getTransactionHeader(method, String(url));
	return fetch(url, { ...init, method, headers: { ...authHeaders(client), ...transaction, ...(init.headers || {}) } });
}

module.exports = { signedFetch };
