# Component B: channels, messages, mentions, read state, events

Routes covered: channel management, message posting, editing, deleting and reactions, paging, replies,
search, pins, mentions, read state, and WebSocket channel events. Check: `bash /spec/checks/check_b.sh`. The rule numbers
below match its output. Component A (auth, workspaces, roles, invitations) must keep passing.

Your service is launched with `/app/start.sh`. The three HTTP nodes listen on `127.0.0.1:8000`,
`:8001`, and `:8002`. All routes are under `/api/...`, return JSON, and take
`Authorization: Bearer <token>`. Every request must answer within 3 seconds.
WebSocket: `ws://127.0.0.1:800x/api/ws?token=<bearer>`, one JSON frame per message.

Build order: channel management (B1-B22), messages and paging (B23-B37), search and pins
(B38-B45), mentions (B46-B53), events (B54-B63), read state (B64-B67), extra length and
paging rules (B68-B72), API conventions for every route (B73-B78), edit, delete and reactions
(B79-B89), and the matching events (B90-B93).

## Channel management (B1-B22)

- **B1** `POST /api/workspaces/{slug}/channels` with `{name}` returns `201` and `{channel}` with
  `id, workspace_id, name, is_private, is_dm, topic, is_archived` (public, not a DM, not archived).
- **B2** A new channel's `topic` is `""`.
- **B3** `{name, topic}` with a topic of 250 characters or fewer keeps that topic.
- **B4** A topic of 251 characters returns `400`.
- **B5** An invalid name (`"Bad Name"`) returns `400`.
- **B6** A duplicate name in the same workspace returns `409`.
- **B7** `GET /api/workspaces/{slug}` returns `200 {workspace, channels, read_state}`; every channel
  has the ChannelObj fields and the list includes `general`.
- **B8** A private channel is listed for its members but omitted for non-members.
- **B9** `PATCH /api/channels/{id}` with `{topic}` by a channel member returns `200` with the new topic.
- **B10** The same by the workspace owner who is not in the channel returns `200`.
- **B11** A topic of 251 characters returns `400`.
- **B12** A non-member who is not the owner gets `403`.
- **B13** `GET /api/channels/{id}/members` returns `200 {members}`, each a UserObj
  (`id, username, display_name, timezone, avatar_url, status_text, status_emoji`).
- **B14** `POST /api/channels/{id}/leave` returns `200 {channel}`.
- **B15** Leaving again returns `200` (idempotent).
- **B16** After leaving, the user is no longer in the members list.
- **B17** `POST /api/channels/{id}/archive` by the workspace owner returns `200`, `channel.is_archived` true.
- **B18** A non-owner archiving gets `403`.
- **B19** Archived channels are omitted from `GET /api/workspaces/{slug}` unless `?include_archived=1`.
- **B20** Posting a message to an archived channel returns `423`.
- **B21** `POST /api/channels/{id}/unarchive` by the owner returns `200`, `is_archived` false.
- **B22** After unarchiving, posting returns `201`.

## Messages and paging (B23-B37)

- **B23** `POST /api/channels/{id}/messages` with `{body}` returns `201 {message}` with `id, channel_id,
  author_id, author, body, parent_id, reply_count, created_at, edited_at, files, reactions, mentions`.
  A top-level message has `parent_id` null, `reply_count` 0, `edited_at` null, and empty `files`,
  `reactions`, `mentions`.
- **B24** `created_at` is RFC 3339 UTC (`2026-01-02T03:04:05Z`, optional fraction).
- **B25** An empty `body` returns `400`.
- **B26** `GET /api/channels/{id}/messages` returns `200 {messages, next_cursor}`, newest first;
  `next_cursor` is null when there are no more.
- **B27** `?limit=2` returns the 2 newest and a non-null `next_cursor`.
- **B28** Passing `cursor=<next_cursor>` returns the next older messages.
- **B29** The last page has `next_cursor` null.
- **B30** A cursor issued by one node works on the other two nodes.
- **B31** A cursor stays valid and returns the same page after new messages are posted.
- **B32** A malformed cursor returns `400`.
- **B33** `limit` of 0, -1 or 201 returns `400`.
- **B34** `limit` defaults to 50 (55 messages and no limit gives 50 and a cursor).
- **B35** `offset` and `page` parameters are not supported: each returns `400` or is ignored
  (the same first page as with no parameter).
- **B36** `GET /api/messages/{id}/replies` returns `200 {replies, next_cursor}`, oldest first, each with
  that `parent_id`.
- **B37** The parent's `reply_count` equals its number of replies.

## Search and pins (B38-B45)

- **B38** `GET /api/search?q=` returns `200 {results, next_cursor}` of matching messages, newest first.
- **B39** Search only covers channels the caller can access (no private channels they are not in, no
  workspaces they are not in).
- **B40** `workspace_id=` limits search to that workspace.
- **B41** `POST /api/messages/{id}/pin` returns `200 {pin}` with `message_id, pinned_by, pinned_at`.
- **B42** Pinning the same message again returns `200` and does not duplicate it.
- **B43** `GET /api/channels/{id}/pins` returns `200 {pins, next_cursor}`, newest pin first, each with
  `message`, `pinned_by`, `pinned_at`.
- **B44** `DELETE /api/messages/{id}/pin` returns `200 {"unpinned": true}` and the pin disappears.
- **B45** Listing pins of a private channel as a non-member returns `403`.

## Mentions (B46-B53)

`mentions` on a MessageObj is a list of user ids.

- **B46** `@username` of a workspace member resolves to that user's id.
- **B47** A user who is not in the workspace is not included.
- **B48** Email-shaped (`name@user.com`) and in-word (`hello@user`) text does not match.
- **B49** The author mentioning themselves is not included.
- **B50** Repeated mentions of one user appear once.
- **B51** `@channel` expands to every channel member except the author, without duplicates, also when
  combined with explicit mentions.
- **B52** A mention raises the mentioned user's `read_state.mention_count` in
  `GET /api/channels/{id}/read` (the response is `{read_state: {..., mention_count}}`), from 0 to 1.
- **B53** A self-mention does not raise the author's own `read_state.mention_count`.

## Events (B54-B63)

Client frames: `{"type":"subscribe","channel_id":N}`, `{"type":"resume","channel_id":N,"since_event_id":E}`.

- **B54** Subscribe is answered with `subscribed` carrying `channel_id` and an integer `head_event_id`.
- **B55** A new message is broadcast as `message.created` with `channel_id` and an integer `event_id`.
- **B56** A thread reply is broadcast as `message.reply`.
- **B57** Joining a channel is broadcast as `member.joined`.
- **B58** Leaving a channel is broadcast as `member.left`.
- **B59** Changing the topic is broadcast as `channel.updated`.
- **B60** Archive and unarchive are broadcast as `channel.archived` and `channel.unarchived`.
- **B61** Event ids in a channel are dense: the first is `head_event_id + 1`, then exactly +1 each.
- **B62** Messages posted through nodes 0, 1 and 2 reach subscribers on every node with identical
  consecutive event ids.
- **B63** `resume` on another node replays every event after `since_event_id` in order, then sends
  `resumed` with `head_event_id` equal to the last event id.

## Read state (B64-B67)

- **B64** `GET /api/channels/{id}/read` returns `200 {read_state}` with `channel_id, last_read_event_id,
  unread_count, mention_count`.
- **B65** `POST` with `{last_read_event_id}` moves the cursor forward and returns the updated `{read_state}`.
- **B66** `POST` with a smaller id returns `200` and the cursor does not move backwards.
- **B67** `GET /api/workspaces/{slug}` embeds `read_state` for the channel.

## More channel and paging rules (B68-B72)

- **B68** A channel name longer than 32 characters returns `400`.
- **B69** A topic of exactly 250 characters is accepted (`201`).
- **B70** `GET /api/messages/{id}/replies` accepts `limit` and `cursor`. `limit=2` returns the 2 oldest
  replies and a `next_cursor`; the cursor returns the rest, with `next_cursor` null.
- **B71** `GET /api/search` accepts `limit` and `cursor`; pages are newest-first and follow each other
  with no overlap.
- **B72** `GET /api/channels/{id}/pins` accepts `limit` and `cursor`; `limit=1` returns the newest pin,
  the cursor returns the next one, and the last page has `next_cursor` null.

## API conventions (B73-B78)

These rules apply to every route in Component B, not only the ones tested above.

- **B73** Every authenticated route returns `401` when the `Authorization` header is missing: workspace
  detail, channel create, patch, members, leave, archive, unarchive, messages (post and list), replies,
  search, pins (list, pin, unpin), and read state (get and post).
- **B74** The same routes return `401` when the token is not a real token.
- **B75** A route that names a channel id, message id or workspace slug that does not exist returns `404`.
- **B76** A request that leaves out a required field returns `400`: posting a message with no `body`, and
  creating a channel with no `name`.
- **B77** A body that is not valid JSON (for example `{not json`) returns `400` on the post-message and
  create-channel routes.
- **B78** A channel created through port `8001` and a message posted to it through port `8002` are both
  visible when read through ports `8000` and `8001`, with the same message id.

## Edit, delete and reactions (B79-B89)

Use `PATCH /api/messages/{id}`, `DELETE /api/messages/{id}`, `POST /api/messages/{id}/reactions`
and `DELETE /api/messages/{id}/reactions/{emoji}` (the emoji is URL-encoded in the path).

- **B79** `PATCH /api/messages/{id}` with `{"body":"new text"}` by the author returns `200 {message}` with the
  new `body` and a non-null `edited_at`.
- **B80** The same request by a user who is not the author returns `403`.
- **B81** `DELETE /api/messages/{id}` by the author returns `200 {"deleted":true}`.
- **B82** A deleted message no longer shows its original text in `GET /api/channels/{id}/messages`
  (it is left out, or listed as a tombstone with different text).
- **B83** `DELETE` on an already deleted message returns `200 {"deleted":true}` again.
- **B84** `PATCH` on a deleted message returns `409`.
- **B85** The workspace owner can delete another user's message (`200 {"deleted":true}`).
- **B86** A member who is neither the author nor the workspace owner gets `403` on `DELETE`.
- **B87** `POST /api/messages/{id}/reactions` with `{"emoji":"👍"}` returns `200 {reactions}` that include the
  emoji, even on the first insert.
- **B88** Sending the same reaction again returns `200` and the `reactions` value does not change
  (one reaction per user and emoji).
- **B89** `DELETE /api/messages/{id}/reactions/{emoji}` returns `200 {reactions}` without the caller's
  reaction.

## Events for edit, delete and reactions (B90-B93)

These use the same dense per-channel event sequence as B54-B63. Every one of them advances the channel's
`event_id` by exactly 1 and is sent to subscribers on every node.

- **B90** Editing a message is broadcast as `message.updated`.
- **B91** Deleting a message is broadcast as `message.deleted`.
- **B92** Adding a reaction is broadcast as `reaction.added`.
- **B93** Removing a reaction is broadcast as `reaction.removed`.

Not checked, but required: if a `resume` frame has a `since_event_id` older than the earliest event you
still keep, send `{"type":"resume.gap","channel_id":N,"earliest_event_id":E}` before the replay.