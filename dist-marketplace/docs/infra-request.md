# Infra request — S4PC serverless MCP (Aurora + Lambda + API Gateway)

**For:** the AWS account / cloud-platform owner.
**From:** the S4PC delivery team (EC2-only access; cannot provision RDS/Lambda/API Gateway).
**Goal:** move the S4PC MCP server off the SSH-tunnelled EC2 host to a serverless HTTPS endpoint.
All code is on branch `feature/serverless-lambda-mcp`; the deployable template is
[`lambda/template.yaml`](../lambda/template.yaml) (AWS SAM). Full runbook: [`lambda/README.md`](../lambda/README.md).

The existing EC2 brain keeps running throughout — this is additive, nothing is decommissioned
until we flip one client-side URL.

## Context (already known)

| Item | Value |
|---|---|
| Account | `269204395522` |
| Region | `us-east-1` |
| EC2 instance | `i-0eb18c668bc9bbb33` (private IP 10.35.20.84) |
| EC2 role | `DigitalBrain-EC2-Role` |
| VPC / subnets | _resolve from the instance above — we cannot read them (see below)_ |

> **Confirmed access level:** the EC2 role `DigitalBrain-EC2-Role` has **no provisioning or
> describe permissions** — `sts:GetCallerIdentity` works, but `ec2:DescribeInstances`,
> `ec2:DescribeSubnets`, and `rds:DescribeDBClusters` are all denied. It is scoped to run the
> brain (Bedrock + S3) only. Hence this request: everything below needs account-level rights we
> do not have. Please resolve the VPC/subnets from instance `i-0eb18c668bc9bbb33`.

## What we need you to provision

1. **Aurora Serverless v2 (PostgreSQL)** in the above VPC, across **two private subnets in
   different AZs**. Serverless v2 min 0.5 ACU (or 0 for auto-pause) is fine — low traffic.
2. On that cluster, run once: `CREATE EXTENSION IF NOT EXISTS vector;` (pgvector).
3. A **Secrets Manager secret** holding the DB connection as JSON:
   `{"host","port","dbname","username","password"}`.
4. A **security group for the Lambda** whose ENIs are allowed **inbound 5432** on the Aurora SG.
5. **Egress for the Lambda subnets to reach Bedrock + Secrets Manager** — either a NAT gateway,
   or VPC **interface endpoints** for `com.amazonaws.us-east-1.bedrock-runtime` and
   `com.amazonaws.us-east-1.secretsmanager`.
6. Allow our **EC2 instance's SG inbound 5432** on the Aurora SG too (so we can load the vectors
   from the box during migration).

## The four values to hand back to us

| SAM parameter | What it is |
|---|---|
| `VpcSubnetIds` | the **two** subnet IDs (different AZs) |
| `LambdaSecurityGroupIds` | the Lambda SG ID from step 4 |
| `DbSecretArn` | the Secrets Manager ARN from step 3 |
| `DbHost` (for our load step) | the Aurora cluster endpoint |

## Then — either you deploy, or grant us a scoped role

**You deploy** (simplest): from `lambda/` after we run `bash build.sh` on the EC2 box —
```bash
sam deploy --guided --stack-name s4pc-mcp --capabilities CAPABILITY_IAM --region us-east-1 \
  --parameter-overrides VpcSubnetIds=<a>,<b> LambdaSecurityGroupIds=<sg> DbSecretArn=<arn> \
                        BrainBackend=pgvector SapMode=offline
```

**Or grant us** a role limited to: `cloudformation:*` on stack `s4pc-mcp`, `lambda:*`,
`apigateway:*`, `iam:CreateRole`/`AttachRolePolicy` for the function role, and `s3:*` on the SAM
artifact bucket — and we run the deploy ourselves.

## The Lambda's own runtime permissions (created by the template)

The function role only needs, and only gets: `bedrock:InvokeModel` on the Titan embed model,
`secretsmanager:GetSecretValue` on the one secret above, VPC ENI management
(`AWSLambdaVPCAccessExecutionRole`), and CloudWatch Logs. No LLM API keys anywhere.
