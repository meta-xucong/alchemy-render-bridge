# Alchemy Node Account Status Projection

## Objective and authority

The local Node Agent page at `http://127.0.0.1:8766/` shows whether the exact
authenticated node is bound to an Alchemy account. Alchemy's node-reward binding
store remains the only authority for that relationship. Bridge stores no second
account binding and does not update reward ownership.

## Read flow

1. The local page calls the existing loopback `/api/status` route. The Agent
   keeps its Node bearer on the server side and calls
   `GET /bridge/node/v2/status`.
2. Bridge authenticates the Node bearer and derives the complete
   `pool_id/node_id/executor_instance` tuple from that credential.
3. For this authenticated self-status request only, Bridge signs that tuple and
   a Unix timestamp with an HMAC key derived from its existing reward-read
   secret. It sends a read-only HTTPS request to Alchemy. The raw Node bearer
   and reward-read secret are never sent to Alchemy.
4. Alchemy verifies the signature and 120-second timestamp window, then looks up
   the exact tuple in its active binding table. It returns only
   `available`, `bound`, and the numeric Alchemy user ID. It returns no email,
   display name, reward history, or other node's data.
5. Bridge adds that small projection to the authenticated node-self response.
   The local page renders it as `已绑定 Alchemy 账号 #<id>` or `未绑定`.

## Failure and privacy behavior

- A failed or unconfigured Alchemy lookup produces `account_binding.available =
  false`; Bridge node counters and task execution remain available.
- A successful lookup with no active binding returns `bound = false` and a null
  user ID. Unbinding is reflected on the next status refresh.
- The anonymous `/bridge/status/v1` fleet projection remains unchanged and never
  includes account bindings, user IDs, provider references, or reward totals.
- The local page continues to bind only to IPv4 loopback and uses its existing
  Host, Origin, CSP, and no-store protections.

## Wire contract

Alchemy accepts `POST /api/internal/render-bridge/node-account-binding` with
the exact identity tuple. Bridge supplies
`X-Bridge-Account-Status-Timestamp` and
`X-Bridge-Account-Status-Signature`. The HMAC message is the UTF-8 encoding of
`renderbridge-node-binding-v1`, timestamp, pool ID, node ID, and executor ID,
separated by newline characters. Both services derive the signing key from the
existing matched reward-read secret using the context string
`alchemy-node-binding-status-key-v1`. This is a separate request signature
domain; the bearer token accepted by Bridge's metrics endpoint is not accepted
by the Alchemy endpoint.

The local page polls every five seconds. The Alchemy request is bounded by the
Bridge HTTP client timeout and is read-only. Deploy the Alchemy endpoint before
the Bridge caller; old Bridge versions and Alchemy outages continue to serve the
existing node status without account information.
