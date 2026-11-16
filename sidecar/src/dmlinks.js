'use strict';
// Shared links in DMs.
//
// X's conversation page sends each message as a Thrift-encoded event. A message that is only a
// shared link (e.g. a YouTube URL pasted on its own) has no text field: the URL sits in the
// message's attachment list instead. rettiwt reads only the text field and twimg media, so such a
// message decodes as empty. This wraps rettiwt's decoder and, for an unencrypted message with no
// text and no media, puts the attachment's URLs in as its text.

const decoder = require('rettiwt-api/dist/helper/DMEventDecoder');

const MEDIA_HOST = /^https?:\/\/(?:video|pbs)\.twimg\.com\//i;
const URL_START = /^https?:\/\//i;

// Thrift binary protocol: [type:u8][field id:i16][value], a struct ends with a 0 byte.
const T_BOOL = 2;
const T_BYTE = 3;
const T_I16 = 6;
const T_I32 = 8;
const T_I64 = 10;
const T_STRING = 11;
const T_STRUCT = 12;
const T_LIST = 15;

function need(buf, offset, length) {
	if (offset < 0 || offset + length > buf.length) throw new Error('truncated');
}

// -> [Map(fieldId -> value), nextOffset]. Values: Buffer (string), Map (struct), array (list), or null.
function readStruct(buf, offset) {
	const fields = new Map();
	for (;;) {
		need(buf, offset, 1);
		const type = buf[offset];
		if (type === 0) return [fields, offset + 1];
		need(buf, offset, 3);
		const id = buf.readInt16BE(offset + 1);
		const [value, next] = readValue(buf, offset + 3, type);
		fields.set(id, value);
		offset = next;
	}
}

function readValue(buf, offset, type) {
	switch (type) {
		case T_BOOL:
		case T_BYTE:
			need(buf, offset, 1);
			return [null, offset + 1];
		case T_I16:
			return [null, offset + 2];
		case T_I32:
			return [null, offset + 4];
		case T_I64:
			return [null, offset + 8];
		case T_STRING: {
			need(buf, offset, 4);
			const length = buf.readInt32BE(offset);
			need(buf, offset + 4, length);
			return [buf.subarray(offset + 4, offset + 4 + length), offset + 4 + length];
		}
		case T_STRUCT:
			return readStruct(buf, offset);
		case T_LIST: {
			need(buf, offset, 5);
			const elemType = buf[offset];
			const count = buf.readInt32BE(offset + 1);
			if (count < 0 || count > 10000) throw new Error('bad list');
			const items = [];
			let cursor = offset + 5;
			for (let i = 0; i < count; i++) {
				const [item, next] = readValue(buf, cursor, elemType);
				items.push(item);
				cursor = next;
			}
			return [items, cursor];
		}
		default:
			throw new Error(`unknown type ${type}`);
	}
}

function parse(buf) {
	try {
		const [fields, end] = readStruct(buf, 0);
		return end === buf.length ? fields : null;
	} catch {
		return null;
	}
}

function utf8(value) {
	if (!Buffer.isBuffer(value)) return null;
	const text = value.toString('utf8');
	return text.includes('�') ? null : text;
}

// Every http(s) URL string anywhere under `value`.
function collectUrls(value, out) {
	if (Buffer.isBuffer(value)) {
		const text = utf8(value);
		if (text && URL_START.test(text) && !/\s/.test(text) && !MEDIA_HOST.test(text)) out.push(text);
	} else if (value instanceof Map) {
		for (const child of value.values()) collectUrls(child, out);
	} else if (Array.isArray(value)) {
		for (const child of value) collectUrls(child, out);
	}
	return out;
}

// { id, urls } for an event whose plaintext message carries URLs outside its text field, else null.
// Outer event: 1 = message id, 7.1.100 = encoded payload. Payload: 1 = event, 1.1 = message, whose
// field 1 is the text; anything else (attachments) is searched for URLs.
function sharedLinks(encodedEvent) {
	const outer = parse(Buffer.from(String(encodedEvent), 'base64'));
	const id = outer && utf8(outer.get(1));
	const payload = outer?.get(7)?.get?.(1)?.get?.(100);
	if (!id || !Buffer.isBuffer(payload)) return null;
	const message = parse(payload)?.get(1)?.get?.(1);
	if (!(message instanceof Map)) return null;
	const urls = [];
	for (const [fieldId, value] of message) {
		if (fieldId !== 1) collectUrls(value, urls);
	}
	return urls.length ? { id, urls: [...new Set(urls)] } : null;
}

function fillEmptyMessages(events, messages) {
	const empty = messages.filter((m) => m && !m.text && !m.isEncrypted && !(m.mediaUrls || []).length);
	if (!empty.length) return messages;
	const linksById = new Map();
	for (const event of events) {
		const found = sharedLinks(event);
		if (found) linksById.set(found.id, found.urls);
	}
	for (const message of empty) {
		const urls = linksById.get(String(message.id));
		if (urls) message.text = urls.join('\n');
	}
	return messages;
}

let installed = false;

function install() {
	if (installed) return;
	installed = true;
	const original = decoder.decodeMessages;
	decoder.decodeMessages = (events, options) => fillEmptyMessages(events || [], original(events, options));
}

module.exports = { install, sharedLinks };
