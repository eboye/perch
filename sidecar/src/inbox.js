'use strict';
// Notifications and direct messages.
//
// rettiwt-api reads notifications (as a polling stream only) and DMs (inbox + conversation) but
// cannot send DMs; sending uses the web client's dm/new2.json endpoint via signedFetch.

const { randomUUID } = require('node:crypto');
const { readFileSync, statSync } = require('node:fs');
const { basename, extname } = require('node:path');
const { ResourceType } = require('rettiwt-api/dist/enums/Resource');
const { Extractors } = require('rettiwt-api/dist/collections/Extractors');
const { signedFetch } = require('./xhttp');
const dmlinks = require('./dmlinks');
const demo = require('./demo');

// Shared-link-only messages decode as empty in rettiwt; recover their URLs.
dmlinks.install();

// id -> plain user ({ id, fullName, userName, profileImage, isVerified }), shared by notifications and DMs.
const userCache = new Map();
const MAX_LOOKUP = 50;

function rememberLegacyUser(raw) {
	const id = raw?.id_str;
	if (!id) return;
	userCache.set(id, {
		id,
		fullName: raw.name,
		userName: raw.screen_name,
		profileImage: raw.profile_image_url_https || '',
		isVerified: Boolean(raw.verified || raw.is_blue_verified),
	});
}

async function lookupUsers(client, ids) {
	const wanted = [...new Set(ids.filter(Boolean).map(String))];
	const missing = wanted.filter((id) => !userCache.has(id)).slice(0, MAX_LOOKUP);
	if (missing.length) {
		try {
			for (const user of await client.user.details(missing)) {
				const plainUser = JSON.parse(JSON.stringify(user));
				userCache.set(plainUser.id, plainUser);
			}
		} catch (err) {
			// Names/avatars are cosmetic: fall back to ids rather than failing the whole page.
			console.error('[sidecar] user lookup failed:', err?.message || err);
		}
	}
	return Object.fromEntries(wanted.filter((id) => userCache.has(id)).map((id) => [id, userCache.get(id)]));
}

const isNewer = (a, b) => {
	try {
		return BigInt(a) > BigInt(b);
	} catch {
		return false;
	}
};

function serializeMessage(message) {
	return {
		id: message.id,
		senderId: message.senderId,
		text: message.text || '',
		createdAt: message.createdAt,
		isEncrypted: Boolean(message.isEncrypted),
		mediaUrls: message.mediaUrls || [],
	};
}

const byTime = (a, b) => new Date(a.createdAt) - new Date(b.createdAt);

// Reactions ([{ emoji, senderId }]) and reply targets of a conversation's recent messages, from the
// REST history. Best effort: an empty map if X refuses. -> Map(messageId -> { reactions, replyTo })
async function restDetails(client, conversationId) {
	const details = new Map();
	try {
		const url = new URL(`https://x.com/i/api/1.1/dm/conversation/${encodeURIComponent(conversationId)}.json`);
		url.search = new URLSearchParams({ count: '100', include_groups: 'true', supports_reactions: 'true', supports_edit: 'true', dm_users: 'false' });
		const res = await signedFetch(client, url);
		if (!res.ok) return details;
		const body = await res.json();
		for (const entry of body.conversation_timeline?.entries || []) {
			const message = entry.message;
			if (!message?.id) continue;
			const reply = message.message_data?.reply_data;
			details.set(String(message.id), {
				reactions: (message.message_reactions || []).map((r) => ({ emoji: r.emoji_reaction || r.reaction_key, senderId: String(r.sender_id) })).filter((r) => r.emoji),
				replyTo: reply?.id ? { id: String(reply.id), text: reply.text || '' } : null,
			});
		}
	} catch (err) {
		console.error('[sidecar] reactions unavailable:', err?.message || err);
	}
	return details;
}

// DM images: X's limits for a photo in a message.
const DM_IMAGE_TYPES = { '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp' };
const DM_IMAGE_MAX_BYTES = 5 * 1024 * 1024;
const UPLOAD_URL = 'https://upload.x.com/i/media/upload.json';
const DM_MEDIA_MAX_BYTES = 15 * 1024 * 1024;

function checkDmImage(path) {
	const type = DM_IMAGE_TYPES[extname(String(path)).toLowerCase()];
	if (!type) throw new Error('Pick a JPEG, PNG or WebP image');
	let size;
	try {
		size = statSync(path).size;
	} catch {
		throw new Error('Couldn’t read the image file');
	}
	if (size > DM_IMAGE_MAX_BYTES) throw new Error('The image is larger than 5 MB');
	return { type, size };
}

// Upload a photo for a DM (INIT / APPEND / FINALIZE with the dm_image category, as x.com does).
// rettiwt's upload has no category, which X needs to attach media to a message. -> media id
async function uploadDmImage(client, path) {
	const { type, size } = checkDmImage(path);
	const post = async (params, body) => {
		const url = new URL(UPLOAD_URL);
		url.search = new URLSearchParams(params);
		const res = await signedFetch(client, url, { method: 'POST', body });
		const text = await res.text();
		if (!res.ok) throw Object.assign(new Error(`Image upload failed (HTTP ${res.status})`), { status: res.status });
		return text ? JSON.parse(text) : {};
	};
	const init = await post({ command: 'INIT', total_bytes: String(size), media_type: type, media_category: 'dm_image' });
	const id = init.media_id_string;
	if (!id) throw new Error('X did not start the image upload');
	const form = new FormData();
	form.append('media', new Blob([readFileSync(path)], { type }), basename(path));
	await post({ command: 'APPEND', media_id: id, segment_index: '0' }, form);
	const done = await post({ command: 'FINALIZE', media_id: id });
	if (done.processing_info?.state === 'failed') throw new Error('X could not process the image');
	return id;
}

// A message from the REST conversation timeline, in serializeMessage's shape. t.co links are
// expanded; a link that only stands for an attached photo/video is dropped (the media is listed).
function serializeRestMessage(message) {
	const data = message.message_data || {};
	let text = data.text || '';
	const media = data.attachment?.photo || data.attachment?.video || data.attachment?.animated_gif;
	for (const link of data.entities?.urls || []) {
		if (link.url && link.expanded_url) text = text.split(link.url).join(link.expanded_url);
	}
	if (media?.url) text = text.split(media.url).join('');
	const mediaUrls = media?.media_url_https ? [media.media_url_https] : [];
	return {
		id: String(message.id || data.id),
		senderId: String(data.sender_id || ''),
		text: text.trim(),
		createdAt: new Date(Number(message.time || data.time)).toISOString(),
		isEncrypted: false,
		mediaUrls,
	};
}

// Cursor for the next (older) page of the inbox, or '' at the end. rettiwt's first-page cursor is
// the inbox's live-update cursor, which returns an empty page when used for paging; older
// conversations page by the trusted timeline's min_entry_id instead.
function olderCursor(raw, currentCursor) {
	const timeline = raw.inbox_initial_state ? raw.inbox_initial_state.inbox_timelines?.trusted : raw.inbox_timeline;
	if (!timeline || timeline.status !== 'HAS_MORE' || !timeline.min_entry_id) return '';
	return timeline.min_entry_id === currentCursor ? '' : String(timeline.min_entry_id);
}

function methods({ requireClient, demoMode }) {
	return {
		// { cursor? } -> { list: [{ id, message, receivedAt, icon, url, from[], target[] }], next, users }
		notifications: async ({ cursor }) => {
			if (demoMode) return demo.notifications(cursor);
			const client = requireClient();
			const resource = ResourceType.USER_NOTIFICATIONS;
			const response = await client.user.request(resource, { count: 40, cursor: cursor || undefined });
			const data = Extractors[resource](response.data);
			const list = data.list.map((n) => ({
				id: n.id,
				message: n.message,
				receivedAt: n.receivedAt,
				from: n.from,
				target: n.target,
				icon: n.raw?.notification_icon || null,
				url: n.raw?.notification_url?.url || null,
			}));
			const users = await lookupUsers(client, list.flatMap((n) => n.from.slice(0, 5)));
			return { list, next: data.next || '', users };
		},

		// { cursor?, meId } -> { conversations: [...], cursor, users }
		'dm.inbox': async ({ cursor, meId }) => {
			if (demoMode) return demo.inbox(cursor);
			const client = requireClient();
			const inbox = await client.dm.inbox(cursor || undefined);
			const raw = inbox.raw || {};
			const state = raw.inbox_initial_state || raw.inbox_timeline || {};
			for (const user of Object.values(state.users || {})) rememberLegacyUser(user);

			const rawConversations = state.conversations || {};

			const conversations = inbox.conversations.map((c) => {
				const messages = c.messages.map(serializeMessage).sort(byTime);
				const last = messages[messages.length - 1] || null;
				const rawConversation = rawConversations[c.id] || {};
				// Unread = newer than this conversation's own read marker, which X updates wherever you
				// read it (web, phone, Perch). The inbox-level "last seen" only drives the envelope badge.
				const lastRead = rawConversation.last_read_event_id || null;
				const lastSeen = c.trusted ? inbox.trustedLastSeenEventId || inbox.lastSeenEventId : inbox.untrustedLastSeenEventId;
				const marker = lastRead || lastSeen;
				const newestEventId = rawConversation.max_entry_id || last?.id || null;
				return {
					id: c.id,
					type: c.type,
					name: c.name || null,
					avatarUrl: c.avatarUrl || null,
					participants: c.participants,
					trusted: c.trusted,
					muted: c.muted,
					lastActivityAt: c.lastActivityAt,
					lastMessage: last,
					lastReadEventId: lastRead,
					newestEventId,
					unread: Boolean(last && last.senderId !== meId && marker && isNewer(newestEventId, marker)),
				};
			});
			conversations.sort((a, b) => new Date(b.lastActivityAt) - new Date(a.lastActivityAt));
			const users = await lookupUsers(client, conversations.flatMap((c) => c.participants));
			const newestEventId = conversations
				.filter((c) => c.trusted && c.newestEventId)
				.reduce((max, c) => (!max || isNewer(c.newestEventId, max) ? c.newestEventId : max), null);
			return {
				conversations,
				cursor: olderCursor(raw, cursor),
				users,
				newestEventId,
				lastSeenEventId: inbox.trustedLastSeenEventId || inbox.lastSeenEventId || null,
			};
		},

		// Marks a conversation read up to `eventId` on X (as x.com does when you open it). If the
		// account has read receipts on, the other participant(s) see it as read.
		// { conversationId, eventId } -> { ok }
		'dm.markRead': async ({ conversationId, eventId }) => {
			if (!conversationId || !eventId) throw new Error('Missing conversation or event id');
			if (demoMode) return { ok: true };
			const client = requireClient();
			const url = `https://x.com/i/api/1.1/dm/conversation/${encodeURIComponent(conversationId)}/mark_read.json`;
			const res = await signedFetch(client, url, {
				method: 'POST',
				headers: { 'content-type': 'application/x-www-form-urlencoded' },
				body: new URLSearchParams({ conversationId: String(conversationId), last_read_event_id: String(eventId) }).toString(),
			});
			if (!res.ok) {
				const text = (await res.text().catch(() => '')).slice(0, 200);
				throw Object.assign(new Error(`X did not accept the read marker (HTTP ${res.status}) ${text}`.trim()), { status: res.status });
			}
			return { ok: true };
		},

		// Clears the DM badge on X: everything up to `eventId` has been seen in the inbox.
		// { eventId } -> { ok }
		'dm.markSeen': async ({ eventId }) => {
			if (!eventId) throw new Error('Missing event id');
			if (demoMode) return { ok: true };
			const client = requireClient();
			const res = await signedFetch(client, 'https://x.com/i/api/1.1/dm/update_last_seen_event_id.json', {
				method: 'POST',
				headers: { 'content-type': 'application/x-www-form-urlencoded' },
				body: new URLSearchParams({ last_seen_event_id: String(eventId), trusted_last_seen_event_id: String(eventId) }).toString(),
			});
			if (!res.ok) {
				const text = (await res.text().catch(() => '')).slice(0, 200);
				throw Object.assign(new Error(`X did not accept the seen marker (HTTP ${res.status}) ${text}`.trim()), { status: res.status });
			}
			return { ok: true };
		},

		// { id } -> { messages (oldest first), hasMore, cursor, users }
		'dm.conversation': async ({ id }) => {
			if (demoMode) return demo.conversation(id);
			const client = requireClient();
			const conversation = await client.dm.conversation(String(id));
			if (!conversation) throw Object.assign(new Error('Conversation not found'), { kind: 'not_found' });
			const messages = conversation.messages.map(serializeMessage).sort(byTime);
			// Reactions come from the REST history (X's newer event log records adding a reaction but
			// not removing it); replies from the event log, else the REST history.
			const [users, rest] = await Promise.all([lookupUsers(client, conversation.participants), restDetails(client, String(id))]);
			for (const message of messages) {
				const extra = rest.get(String(message.id));
				message.replyTo = dmlinks.replyFor(message.id) || extra?.replyTo || null;
				message.reactions = extra?.reactions || [];
			}
			// Older pages are not exposed: X's GetConversationPage cursor is a local sequence id hidden
			// inside encoded XChat events, so paging by message id returns nothing. The first page
			// (~150 recent messages) is what the UI shows.
			return {
				id: conversation.id,
				messages,
				hasMore: false,
				cursor: '',
				participants: conversation.participants,
				users,
			};
		},

		// React to a message with an emoji, or take the reaction back. { conversationId, messageId, emoji, remove } -> { ok }
		'dm.react': async ({ conversationId, messageId, emoji, remove }) => {
			if (!conversationId || !messageId || !emoji) throw new Error('Missing conversation, message or emoji');
			if (demoMode) return demo.react(String(messageId), String(emoji), Boolean(remove));
			const url = `https://x.com/i/api/1.1/dm/reaction/${remove ? 'delete' : 'new'}.json`;
			const res = await signedFetch(requireClient(), url, {
				method: 'POST',
				headers: { 'content-type': 'application/x-www-form-urlencoded' },
				body: new URLSearchParams({ reaction_key: 'emoji', emoji_reaction: String(emoji), conversation_id: String(conversationId), dm_id: String(messageId) }).toString(),
			});
			if (!res.ok) throw Object.assign(new Error(`X did not ${remove ? 'remove' : 'add'} the reaction (HTTP ${res.status})`), { status: res.status });
			return { ok: true };
		},

		// A photo attached to a DM. X serves these only to signed-in members of the conversation, so
		// it's fetched here with the session. Only X's DM media URLs are accepted: the session's
		// cookies must never go to any other host. { url } -> { type, data (base64) }
		'dm.media': async ({ url }) => {
			if (!dmlinks.DM_MEDIA.test(String(url || ''))) throw new Error('Not a DM image');
			if (demoMode) return demo.dmMedia();
			const res = await signedFetch(requireClient(), new URL(url));
			if (!res.ok) throw Object.assign(new Error(`Couldn’t load the image (HTTP ${res.status})`), { status: res.status });
			const type = res.headers.get('content-type') || '';
			if (!type.startsWith('image/')) throw new Error('X did not return an image');
			const bytes = Buffer.from(await res.arrayBuffer());
			if (bytes.length > DM_MEDIA_MAX_BYTES) throw new Error('The image is too large to show');
			return { type, data: bytes.toString('base64') };
		},

		// Messages older than `before` (a message id), from X's REST conversation history, which pages
		// back to the start (the GraphQL page the conversation opens with can't).
		// { id, before } -> { messages (oldest first), cursor ('' at the start), users }
		'dm.older': async ({ id, before }) => {
			if (!id || !before) throw new Error('Missing conversation or message id');
			if (demoMode) return demo.olderMessages(id, before);
			const client = requireClient();
			const url = new URL(`https://x.com/i/api/1.1/dm/conversation/${encodeURIComponent(String(id))}.json`);
			url.search = new URLSearchParams({
				max_id: String(before),
				count: '100',
				context: 'FETCH_DM_CONVERSATION_HISTORY',
				include_groups: 'true',
				include_conversation_info: 'true',
				dm_users: 'true',
			});
			const res = await signedFetch(client, url);
			if (!res.ok) throw Object.assign(new Error(`Couldn’t load earlier messages (HTTP ${res.status})`), { status: res.status });
			const timeline = (await res.json())?.conversation_timeline || {};
			for (const user of Object.values(timeline.users || {})) rememberLegacyUser(user);
			const messages = (timeline.entries || [])
				.map((entry) => entry.message)
				.filter((m) => m?.message_data && isNewer(before, m.id))
				.map(serializeRestMessage)
				.sort(byTime);
			const more = timeline.status === 'HAS_MORE' && timeline.min_entry_id && timeline.min_entry_id !== String(before);
			const users = await lookupUsers(client, messages.map((m) => m.senderId));
			return { messages, cursor: more ? String(timeline.min_entry_id) : '', users };
		},

		// Delete a conversation from your inbox (the other people in it keep their copy). { id } -> { ok }
		'dm.deleteConversation': async ({ id }) => {
			if (!id) throw new Error('Missing conversation id');
			if (demoMode) return { ok: true };
			// rettiwt posts to dm/<id>/delete.json, which X answers with 404; x.com uses this one.
			const url = `https://x.com/i/api/1.1/dm/conversation/${encodeURIComponent(String(id))}/delete.json`;
			const res = await signedFetch(requireClient(), url, {
				method: 'POST',
				headers: { 'content-type': 'application/x-www-form-urlencoded' },
				body: new URLSearchParams({ dm_secret_conversations_enabled: 'false', krs_registration_enabled: 'false', include_groups: 'true' }).toString(),
			});
			if (!res.ok) {
				const body = await res.json().catch(() => ({}));
				const reason = body.errors?.[0]?.message || `HTTP ${res.status}`;
				throw Object.assign(new Error(`X did not delete the conversation (${reason})`), { status: res.status });
			}
			return { ok: true };
		},

		// { conversationId, text, mediaPath?, replyTo? (message id) } -> { id, createdAt }. One image per message (X's limit).
		'dm.send': async ({ conversationId, text, mediaPath, replyTo }) => {
			if (!conversationId) throw new Error('Missing conversation id');
			if (!text?.trim() && !mediaPath) throw new Error('Message is empty');
			if (mediaPath) checkDmImage(mediaPath);
			if (demoMode) return { id: String(Date.now()), createdAt: new Date().toISOString() };
			const client = requireClient();
			const mediaId = mediaPath ? await uploadDmImage(client, mediaPath) : null;
			const url = new URL('https://x.com/i/api/1.1/dm/new2.json');
			url.search = new URLSearchParams({
				ext: 'mediaColor,altText,mediaStats,highlightedLabel',
				include_ext_alt_text: 'true',
				include_reply_count: '1',
				tweet_mode: 'extended',
				include_groups: 'true',
				include_inbox_timelines: 'true',
				supports_reactions: 'true',
				supports_edit: 'true',
			});
			const res = await signedFetch(client, url, {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({
					conversation_id: String(conversationId),
					recipient_ids: false,
					request_id: randomUUID(),
					text: text || '',
					...(mediaId ? { media_id: mediaId } : {}),
					...(replyTo ? { reply_to_dm_id: String(replyTo) } : {}),
					cards_platform: 'Web-12',
					include_cards: 1,
					include_quote_count: true,
					dm_users: false,
				}),
			});
			const body = await res.json().catch(() => ({}));
			if (!res.ok || body.errors) {
				const reason = body.errors?.[0]?.message || `HTTP ${res.status}`;
				throw Object.assign(new Error(`X did not accept the message (${reason})`), { status: res.status });
			}
			const sent = body.entries?.find((e) => e.message)?.message;
			return {
				id: sent?.message_data?.id || sent?.id || String(Date.now()),
				createdAt: sent?.time ? new Date(Number(sent.time)).toISOString() : new Date().toISOString(),
			};
		},
	};
}

module.exports = { methods, uploadDmImage };
