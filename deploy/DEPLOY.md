# Deploying the Clairoscope on AWS

One container (FastAPI API + web UI on port 8080). Two supported paths, matching the brief's
"REPH-approved AWS environment using EC2 or ECS":

| | **A. EC2 (fastest, recommended for the demo)** | **B. ECS Fargate (+ load balancer)** |
|---|---|---|
| Setup time | ~10 min | ~30-45 min |
| Needs Docker on your laptop | No (builds on the instance) | No if built on EC2 / CodeBuild |
| URL | `http://<public-ip>:8080` | `http://<alb-dns-name>` |

## 0. Shared prerequisites

1. **Region**: `ap-southeast-2` (the team's Bedrock access is there).
2. **Bedrock model access**: `global.anthropic.claude-opus-4-6-v1` (verified working for our account).
3. **Data in S3** (stays inside the event AWS account, per brief §7). From a machine with the extracted data:
   ```bash
   aws s3 sync data/ s3://CHANGE-ME-BUCKET/auditor-data/ --exclude "*" --include "*.parquet" --include "_docs/*"
   ```
4. **IAM role** with [`iam-policy.json`](iam-policy.json) (replace `CHANGE-ME-BUCKET`): Bedrock invoke + read
   the data prefix. Use it as the EC2 instance profile (A) or the ECS task role (B).
   With a role, **no Bedrock key is needed** in the app; credentials rotate automatically.
   If the event only gives you a Bedrock API key, pass it as `AWS_BEARER_TOKEN_BEDROCK` instead (short-term keys
   stop working when the console session that created them ends; prefer a long-term key. To swap keys, update the
   value and select the red Claude status in the UI to reload).

## A. EC2 (Amazon Linux 2023)

1. Launch an instance: Amazon Linux 2023, `t3.large` (2 vCPU / 8 GB), 30 GB disk, the IAM role from step 0.4.
2. Security group: allow TCP **8080** from your team / judges' IP range (not `0.0.0.0/0` unless needed).
3. Paste [`ec2-user-data.sh`](ec2-user-data.sh) as **User data** after editing the `CHANGE-ME` values
   (`DATA_S3_URI`, `BASIC_AUTH`). If the GitHub repo is private, zip the repo, upload it to S3, and set
   `SOURCE_S3_URI` instead of cloning.
4. Wait ~5 minutes, then open `http://<public-ip>:8080`. Log in with the `BASIC_AUTH` user/password.
5. Check health: `curl http://<public-ip>:8080/api/health` → `"status":"ok"`, and the Claude status in the UI is green.

Update after a code change: SSH/SSM into the instance, `cd /opt/auditor && git pull`, then re-run the
`docker build` and `docker run` lines from the script.

## B. ECS Fargate

1. **Image**: run [`build-and-push.sh`](build-and-push.sh) from the repo root on a machine with Docker + AWS CLI
   (the EC2 instance from path A works). It creates the ECR repo `clairoscope` and pushes `:latest`.
2. **Secret** (optional login): `aws secretsmanager create-secret --name clairoscope/basic-auth --secret-string 'demo:CHANGE-ME'`.
   The ECS *execution* role needs `secretsmanager:GetSecretValue` on it and `logs:CreateLogGroup`.
3. **Task definition**: edit [`ecs-task-definition.json`](ecs-task-definition.json) (`CHANGE-ME-ACCOUNT`,
   `CHANGE-ME-BUCKET`) and register it:
   `aws ecs register-task-definition --cli-input-json file://deploy/ecs-task-definition.json`
4. **Service**: create an ECS service (Fargate, 1 task) behind an Application Load Balancer:
   target group on port 8080, health check path `/api/health`, listener 80 (or 443 with ACM).
   Keep **desired count = 1**: audit jobs live in the task's memory.
5. Open the ALB DNS name.

## Configuration reference

| Variable | Purpose |
|---|---|
| `AWS_REGION` | Bedrock region (required) |
| `AUDITOR_LLM` | `bedrock` (default when AWS settings exist) / `off` |
| `AUDITOR_BEDROCK_MODEL` / `AUDITOR_BEDROCK_API` | model ID / `invoke` or `mantle` |
| `AWS_BEARER_TOKEN_BEDROCK` | only if not using an IAM role |
| `AUDITOR_DATA_S3_URI` | where the container downloads the Parquet data at start |
| `AUDITOR_BASIC_AUTH` | `user:password` browser login (recommended on any public URL) |
| `AUDITOR_STATE_DIR` | decision ledger location (`/app/state`; mount a volume to keep it) |
| `AUDITOR_EFFORT` | `low` / `medium` / `high` (lower = faster audits) |

## Before the demo

- [ ] `/api/health` returns ok and the UI's Claude status is green ("Claude ready").
- [ ] Run one flagship audit with the AI agent on (about 30-40 s) and one typed claim.
- [ ] If the Claude status turns red, the app keeps working in rule-based mode and says so on every result.
- [ ] Note the public URL and login for the judges.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Container exits: `no data ... AUDITOR_DATA_S3_URI not set` | Set `AUDITOR_DATA_S3_URI`, or build the image with `./data` present |
| `AccessDenied` on S3 in the logs | Role lacks `s3:ListBucket` / `s3:GetObject` on the data prefix |
| Claude status red: `Credentials rejected` | Role lacks Bedrock permissions, or the bearer key expired (short-term keys end with their console session): regenerate |
| Claude status red: `not available for this account` | Model access not enabled; use `global.anthropic.claude-opus-4-6-v1` |
| EC2: AI works on the host but not in the container | Use `--network host` (as in the script) or raise the IMDSv2 hop limit to 2 |
| 401 in the browser | `AUDITOR_BASIC_AUTH` is set: log in with that user/password |
