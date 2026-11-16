'use strict';
// Notifications and direct messages.
//
// rettiwt-api reads notifications (as a polling stream only) and DMs (inbox + conversation) but
// cannot send DMs; sending uses the web client's dm/new2.json endpoint via signedFetch.

const { randomUUID } = require('node:crypto');
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
			if (demoMode) return demo.inbox();
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
				cursor: inbox.cursor || '',
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
			const users = await lookupUsers(client, conversation.participants);
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

		// Delete a conversation from your inbox (the other people in it keep their copy). { id } -> { ok }
		'dm.deleteConversation': async ({ id }) => {
			if (!id) throw new Error('Missing conversation id');
			if (demoMode) return { ok: true };
			await requireClient().dm.deleteConversation(String(id));
			return { ok: true };
		},

		// { conversationId, text } -> { id, createdAt }
		'dm.send': async ({ conversationId, text }) => {
			if (!conversationId) throw new Error('Missing conversation id');
			if (!text?.trim()) throw new Error('Message is empty');
			if (demoMode) return { id: String(Date.now()), createdAt: new Date().toISOString() };
			const client = requireClient();
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
					text,
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

module.exports = { methods };
