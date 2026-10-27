# Auth: register, login, me

Routes covered: `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me`.
Check: `bash /spec/auth-single-file/checks/check_auth.sh`. The rule numbers below match its output.

Your service is launched with `/app/start.sh`. The three HTTP nodes listen on
`127.0.0.1:8000`, `:8001`, and `:8002`.

## The nine rules

1. **Register.** `POST /api/auth/register` with body `{username, password}`, a valid
   username and a password of 8 or more characters, returns `201` and JSON with `user` and
   `token`. `user.username` is the username sent. `token` is at least 16 characters.

2. **Duplicate username.** Registering the same username a second time returns `409`.

3. **Invalid username.** A username containing a space returns `400`.

4. **Short password.** A password of 7 characters returns `400`.

5. **Login.** `POST /api/auth/login` with body `{username, password}` and the right
   password returns `200` and JSON with `user` and `token`.

6. **Wrong password.** Login with the wrong password returns `401`. Not `200`, not `400`.

7. **Me.** `GET /api/auth/me` with header `Authorization: Bearer <token>` returns `200` and
   JSON with `user`, where `user.username` is the username that registered.

8. **Me without a valid token.** `GET /api/auth/me` with no `Authorization` header returns
   `401`. So does a made-up token.

9. **Every node.** A user registered on port `8000` can log in on port `8002`. A token
   issued on port `8000` is accepted by `GET /api/auth/me` on ports `8001` and `8002`.

Every request must answer within 5 seconds.
