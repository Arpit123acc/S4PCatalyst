# Giving the delivery team access to the Brain UI

**Status:** blocked on one IAM change (§3). Everything else is verified working.
**Purpose:** replace the shared `plink` SSH tunnel with per-user, audited access to the
read-only Brain Explorer, without opening an inbound port.

Companion to [brain-endpoint-setup.md](brain-endpoint-setup.md), which covers the *MCP
endpoint for agents*. This document covers *people in a browser* — a different front
door, because an API key in a header is not something a browser session carries.

---

## 1. What may be exposed, and what may not

The three services on this host have very different exposure profiles, and treating them
uniformly is the mistake to avoid.

| Service | Port | Auth today | Verdict |
|---|---|---|---|
| **brain-ui** | 8400 | **none** | The one to share. Read-only, but it renders masked learning document *snippets*, so it still needs a control in front. Its own `--host` help says: "put it behind a proxy that terminates TLS and authenticates." |
| **s4pc-webapp** | 8321 | none | **Do not expose.** It holds the pipeline's human-approval controls. `deploy/ecosystem.config.js` keeps it a separate process precisely so a demo surface cannot reach them. |
| **MCP server** | 3002 | implemented, **disabled** | Agents only, via brain-endpoint-setup.md §4. Never move it off loopback without setting `S4PC_API_KEYS` **in the same change** — the server logs a boxed warning and an `insecure_bind` audit event if you do. |

Every brain-ui search is a Bedrock Titan call and brain-ui has no rate limiting, so an
exposed endpoint is a cost surface as well as a data one. Under §2 that is bounded by who
holds IAM access; under §4 it needs a WAF rate rule.

## 2. Chosen approach — SSM Session Manager port forwarding

```
teammate's laptop                                    EC2 (private subnet)
┌──────────────────┐                                ┌────────────────────┐
│ aws ssm          │   TLS, outbound only           │ amazon-ssm-agent   │
│ start-session ───┼───► ssmmessages.us-east-1 ◄────┼─── (already        │
│                  │        (AWS managed)           │     installed and  │
│ localhost:8400 ──┼────────── tunnelled ───────────┼──► 127.0.0.1:8400  │
└──────────────────┘                                └────────────────────┘
```

Why this over a load balancer:

- **No inbound port, no security-group change, no ALB, no certificate, no DNS record.**
- **Per-user identity.** Access is the teammate's own IAM principal, and every session is
  attributed in CloudTrail. The current `plink` tunnel is a shared credential with no
  attribution — that is the actual problem being solved, not the convenience.
- **brain-ui keeps binding loopback**, so its missing authentication never becomes
  reachable. The control is IAM, not the application.
- It travels: the same mechanism works from any machine with the CLI, on or off the VPN.

Per-teammate prerequisites: the AWS CLI, **plus the Session Manager plugin**, which is a
separate install from the CLI and the usual thing that trips people up.

```bash
aws ssm start-session \
  --target i-0eb18c668bc9bbb33 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8400"],"localPortNumber":["8400"]}'
```

Then open <http://localhost:8400>.

## 3. The blocker — one IAM policy attachment

Verified 2026-09-08. The SSM agent is **installed and running** (`systemctl is-active
amazon-ssm-agent` → `active`) but cannot register, so the instance does not appear in
`describe-instance-information` and Session Manager cannot target it.

From `/var/log/amazon/ssm/amazon-ssm-agent.log`, repeating every ~28 minutes:

```
AccessDeniedException: User: arn:aws:sts::269204395522:assumed-role/
  DigitalBrain-EC2-Role/i-0eb18c668bc9bbb33 is not authorized to perform:
  ssm:UpdateInstanceInformation on resource:
  arn:aws:ec2:us-east-1:269204395522:instance/i-0eb18c668bc9bbb33
  because no identity-based policy allows the ssm:UpdateInstanceInformation action
```

**This is purely IAM. The network path already works** — the agent received an HTTP 400
from the SSM API, which it could only do by reaching it. No VPC interface endpoints
(`ssm`, `ssmmessages`, `ec2messages`), no proxy allowlist change, no NAT change.

A second line records the fallback that is also closed:

```
RequestManagedInstanceRoleToken: AccessDeniedException: Systems Manager's
  instance management role is not configured for account: 269204395522
```

That is Default Host Management Configuration, an account-level setting. **Do not ask for
it.** It would apply to every instance in the account (38+ registered at time of writing),
which is a far larger blast radius than one policy on one role.

### 3.1 Ask A — let the instance register

> Attach `arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore` to role
> **`DigitalBrain-EC2-Role`** in account **269204395522**.

The AWS-managed policy is recommended over a hand-rolled one: it is the documented path,
so it will not be bounced for being non-standard. If the cloud team prefers least
privilege, the minimum for port forwarding is `ssm:UpdateInstanceInformation`,
`ssm:ListAssociations`, `ssm:ListInstanceAssociations`, and the four
`ssmmessages:{Create,Open}{Control,Data}Channel` actions.

### 3.2 Ask B — let the team open a session, and **only** to port 8400

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "StartPortForwardToBrainUiOnly",
      "Effect": "Allow",
      "Action": "ssm:StartSession",
      "Resource": [
        "arn:aws:ec2:us-east-1:269204395522:instance/i-0eb18c668bc9bbb33",
        "arn:aws:ssm:us-east-1::document/AWS-StartPortForwardingSession"
      ],
      "Condition": {
        "NumericEquals": { "ssm:SessionDocumentPortNumber": 8400 }
      }
    },
    {
      "Sid": "ManageOwnSessions",
      "Effect": "Allow",
      "Action": ["ssm:TerminateSession", "ssm:ResumeSession"],
      "Resource": "arn:aws:ssm:us-east-1:269204395522:session/${aws:username}-*"
    }
  ]
}
```

**The port condition is not optional.** Without it the same grant reaches 3002 (the MCP,
auth disabled) and 8321 (the pipeline's approval controls) on the same host, turning
"view the brain" into "reach anything listening there". Note also that
`AWS-StartSSHSession` and `SSM-SessionManagerRunShell` are deliberately absent: this grant
is a port forward, not a shell.

## 4. If browser-native access is wanted later

An internal ALB authenticating at the edge, so brain-ui still needs no code change:

```
team browser ──HTTPS──► internal ALB ──────► EC2:8400
  (on corp VPN)          authenticate-oidc → Entra ID
                         + WAF rate limit
```

Cloud-team items: an internal ALB in the existing private subnets, an ACM certificate for
an internal name, a Route 53 private-zone record, and an Entra ID app registration for
OIDC. The app registration is usually the organisationally slow one, not the AWS parts. A
*private* ALB suffices since the team already has the VPN — no public subnets.

Choose this when the CLI step in §2 becomes the friction. It is four items against one, so
it is not the place to start.

## 5. Explicitly out of scope

**Internet-facing exposure.** The corpus holds masked learning documents, and
masking removes PII, not commercial context. brain-endpoint-setup.md §4 Option D already
rejected third-party tunnels (ngrok / Cloudflare Tunnel) on data-processing grounds, and
that reasoning applies more strongly to a UI that renders document snippets to a browser.
Internet exposure is a security and engagement-terms conversation, not a configuration
change.

## 6. Verification once Ask A lands

```bash
# on the host — should now return a row
aws ssm describe-instance-information --no-cli-pager \
  --filters "Key=InstanceIds,Values=i-0eb18c668bc9bbb33" \
  --query "InstanceInformationList[].[InstanceId,PingStatus,AgentVersion]" --output table

# from a laptop — should open and hold
aws ssm start-session --target i-0eb18c668bc9bbb33 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8400"],"localPortNumber":["8400"]}'

# and the negative test, which MUST fail with AccessDenied
aws ssm start-session --target i-0eb18c668bc9bbb33 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["3002"],"localPortNumber":["3002"]}'
```

The third command is the one that proves the port condition is doing its job. Run it and
confirm it is refused before telling anyone the access is scoped.
