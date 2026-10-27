# Component A: roles and invitations

Routes covered: auth, workspaces, workspace members and roles, ownership transfer, invitations,
join mode, plus the basic channel create and join routes the role rules depend on.
Check: `bash /spec/checks/check_a.sh`. The rule numbers below match its output.

Your service is launched with `/app/start.sh`. The three HTTP nodes listen on
`127.0.0.1:8000`, `:8001`, and `:8002`. All routes are under `/api/...`, return JSON, and take
`Authorization: Bearer <token>` on authenticated routes. Every request must answer within 3 seconds.

Build order: auth (1-9), workspaces (10-21), basic channels (22-26), roles (27-36),
role-based channel permissions (37-41), invitations (42-55), join mode (56-62),
ownership transfer (63-67). Rules 68-72 extend auth and invitations. Rules 73-78 are API conventions that apply to every route. Rules 22-26 and 37-41 use channel routes: build `POST
/api/workspaces/{slug}/channels` and `POST /api/channels/{id}/join` before the roles work.

Roles are `owner`, `admin`, `member`, `guest`. The workspace creator is the `owner`. A user who
accepts an invitation becomes a `member`.

## Auth (1-9)

1. **Register.** `POST /api/auth/register` with `{username, password}`, a valid username and a
   password of 8 or more characters, returns `201` and JSON with `user` and `token`.
   `user.username` is the username sent. `token` is at least 16 characters.
2. **Duplicate username.** Registering the same username again returns `409`.
3. **Invalid username.** A username containing a space returns `400`.
4. **Short password.** A password of 7 characters returns `400`.
5. **Login.** `POST /api/auth/login` with `{username, password}` and the right password returns
   `200` and JSON with `user` and `token`.
6. **Wrong password.** Login with the wrong password returns `401`.
7. **Me.** `GET /api/auth/me` with a valid bearer token returns `200` and JSON with `user`, where
   `user.username` is the username that registered.
8. **Me without a valid token.** `GET /api/auth/me` with no `Authorization` header returns `401`.
   So does a made-up token.
9. **Every node.** A user registered on port `8000` can log in on port `8002`. A token issued on
   port `8000` is accepted by `GET /api/auth/me` on ports `8001` and `8002`.

## Workspaces (10-21)

10. **Create.** `POST /api/workspaces` with `{slug, name}` and a valid kebab-case slug returns
    `201` and JSON with `workspace` and `general_channel`. `workspace` has `id`, `slug`, `name`,
    `owner_id`, `join_mode`. `workspace.slug` is the slug sent.
11. **Default join mode.** `workspace.join_mode` is `"open"` when the workspace is created.
12. **Owner.** `workspace.owner_id` is the id of the user who created it.
13. **General channel.** `general_channel.name` is `"general"`.
14. **Members list.** `GET /api/workspaces/{slug}/members` returns `200` and JSON with `members`.
    Each entry has `user_id` and `role`. The creator's entry has role `owner`.
15. **Duplicate slug.** Creating a workspace with a slug that already exists returns `409`.
16. **Invalid slug.** The slug `A` (uppercase, one character) returns `400`. A slug must match
    `^[a-z0-9-]{2,32}$`.
17. **Create without a token.** `POST /api/workspaces` with no `Authorization` header returns `401`.
18. **List as a member.** `GET /api/workspaces` returns `200` and JSON with `workspaces`, which
    includes every workspace the caller belongs to or owns.
19. **List as a non-member.** The same request from a user who is not in a workspace does not
    include it in `workspaces`.
20. **Every node.** A workspace created through port `8000` appears in `GET /api/workspaces` on
    ports `8001` and `8002`.
21. **Unknown workspace.** `GET /api/workspaces/{slug}/members` for a slug that does not exist
    returns `404`.

## Basic channels (22-26)

22. **Create a public channel.** `POST /api/workspaces/{slug}/channels` with `{name}` as the owner
    returns `201` and JSON with `channel`. `channel.id` is set.
23. **Create a private channel.** The same route with `{name, is_private: true}` as the owner
    returns `201`, and `channel.is_private` is true.
24. **Join an unknown channel.** `POST /api/channels/{id}/join` for an id that does not exist
    returns `404`.
25. **Join a public channel.** A workspace member calling `POST /api/channels/{id}/join` on a
    public channel gets `200`.
26. **Join a private channel without an invite.** A workspace member who was not invited to a
    private channel gets `403`. This holds in every workspace, open ones too: open join (rule 56) applies to
    public channels only, never to private ones.

## Roles (27-36)

All use `PATCH /api/workspaces/{slug}/members/{user_id}` with body `{role}`.

27. **Promote.** The owner sets a member to `admin`. Returns `200` and JSON with
    `member: {user_id, role}`, where `user_id` is the target and `role` is `"admin"`.
28. **Role is stored.** After rule 27, `GET /api/workspaces/{slug}/members` shows that user with
    role `admin`.
29. **Demote.** The owner sets an admin to `member`. Returns `200` with `member.role` `"member"`.
30. **Admin sets guest.** An admin sets a member to `guest`. Returns `200` with `member.role`
    `"guest"`.
31. **Admin vs admin.** An admin changing another admin's role returns `403`.
32. **Admin vs owner.** An admin changing the owner's role returns `403`.
33. **Owner via PATCH.** Setting `role` to `"owner"` returns `400`. Use `transfer_ownership`.
34. **Owner's own role.** The owner changing their own role returns `403`.
35. **Plain member.** A `member` changing anyone's role returns `403`.
36. **Unknown role.** A `role` that is not one of the four roles returns `400`.

## Role-based channel permissions (37-41)

37. **Guest join.** A `guest` calling `POST /api/channels/{id}/join` on a public channel gets `403`.
38. **Guest create.** A `guest` calling `POST /api/workspaces/{slug}/channels` gets `403`.
39. **Member creates public.** A `member` creating a public channel gets `201`.
40. **Member creates private.** A `member` creating a channel with `{is_private: true}` gets `403`.
41. **Admin creates private.** An `admin` creating a channel with `{is_private: true}` gets `201`.

## Invitations (42-55)

42. **Create.** `POST /api/workspaces/{slug}/invitations` with `{}` as an admin returns `201` and
    JSON with `invitation`, which has a non-empty string `code`.
43. **Member cannot create.** A `member` creating an invitation gets `403`.
44. **List.** `GET /api/workspaces/{slug}/invitations` as the owner returns `200` and JSON with
    `invitations`, which includes the active code.
45. **Member cannot list.** A `member` listing invitations gets `403`.
46. **Accept.** `POST /api/invitations/{code}/accept` with a valid code by a user who is not in the
    workspace returns a `2xx` status. That user then appears in the workspace members list.
47. **Default is one use.** An invitation created with no `max_uses` can be accepted once. A
    second user accepting the same code gets `404`.
48. **Unknown code.** Accepting a code that does not exist returns `404`.
49. **Expired code.** An invitation created with `{"expires_in_seconds": 1}`, accepted after 2
    seconds, returns `404`.
50. **Revoke.** `DELETE /api/workspaces/{slug}/invitations/{code}` as an admin returns `200` and
    JSON `{"deleted": true}`.
51. **Revoked code.** Accepting a revoked code returns `404`.
52. **Member cannot revoke.** A `member` calling the revoke route gets `403`.
53. **Targeted invitation, wrong user.** An invitation created with `{"invited_user_id": X}`
    accepted by a different user returns `403`.
54. **Targeted invitation, right user.** The same invitation accepted by user X returns a `2xx`
    status.
55. **Accept without a token.** `POST /api/invitations/{code}/accept` with no `Authorization`
    header returns `401`.

## Join mode (56-62)

56. **Open workspace.** When `join_mode` is `"open"`, a user who is not a workspace member calling
    `POST /api/channels/{id}/join` on a public channel gets `200` and becomes a `member` of the
    workspace (visible in the members list).
57. **Set invite_only.** `PATCH /api/workspaces/{slug}` with `{"join_mode": "invite_only"}` as the
    owner returns `200` and JSON with `workspace.join_mode` `"invite_only"`.
58. **Admin renames.** `PATCH /api/workspaces/{slug}` with `{"name": "Renamed Workspace"}` as an
    admin returns `200` and `workspace.name` is the new name.
59. **Member cannot patch.** A `member` calling `PATCH /api/workspaces/{slug}` gets `403`.
60. **Unknown join mode.** `{"join_mode": "closed"}` returns `400`.
61. **Invite-only blocks joins.** When `join_mode` is `"invite_only"`, a non-member calling
    `POST /api/channels/{id}/join` gets `403`.
62. **Invite-only with an invitation.** The same user accepts a valid invitation first, then
    `POST /api/channels/{id}/join` returns `200`.

## Ownership transfer (63-67)

All use `POST /api/workspaces/{slug}/transfer_ownership` with body `{new_owner_id}`.

63. **Target must be an admin.** Transferring to a plain `member` returns `400`.
64. **Owner only.** A non-owner (an admin) calling it gets `403`.
65. **Transfer.** The owner transferring to an `admin` returns a `2xx` status.
66. **Roles after transfer.** `GET /api/workspaces/{slug}/members` shows the target as `owner` and
    the previous owner as `admin`.
67. **Old owner.** The previous owner calling transfer again gets `403`.

## More invitation and auth rules (68-72)

68. **max_uses.** An invitation created with `{"max_uses": 2}` can be accepted by two different
    users (each gets a `2xx`). A third user accepting it gets `404`.
69. **Exhausted is not listed.** `GET /api/workspaces/{slug}/invitations` does not include an
    invitation that has used up its `max_uses`.
70. **Revoked is not listed.** The same list does not include a revoked invitation.
71. **Targeted is single-use.** An invitation created with `{"invited_user_id": X, "max_uses": 5}`
    can be accepted once by user X. Accepting it again returns `404`. A targeted invitation has exactly one
    use, whatever `max_uses` says.
72. **Username characters.** Registering a username with a special character (for example
    `name-x!`) returns `400`. A username is letters, digits and underscore only.

## API conventions (73-78)

These rules apply to every route in Component A, not only the ones tested above.

73. **No token.** Every authenticated route returns `401` when the `Authorization` header is missing.
    This includes listing workspaces and members, changing roles, patching a workspace, creating,
    listing and revoking invitations, transferring ownership, creating channels, and joining a channel.
74. **Invalid token.** The same routes return `401` when the token is not a real token.
75. **Unknown ids.** A route that names a workspace slug or a user id that does not exist returns
    `404` (for example, `PATCH /api/workspaces/{unknown}`, an invitation route under an unknown
    workspace, or changing the role of an unknown user id).
76. **Missing fields.** A request that leaves out a required field returns `400`: registering with no
    password, creating a workspace with no `name`, creating a channel with no `name`, and changing a
    role with no `role`.
77. **Malformed JSON.** A body that is not valid JSON (for example `{not json`) returns `400` on the
    register, login, create-workspace and create-channel routes.
78. **Every node.** An invitation created through port `8001` can be accepted through port `8002`, and
    the new member then appears in the members list read through port `8000`.