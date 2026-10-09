#!/bin/bash
# EC2 one-shot setup (Amazon Linux 2023). Paste as "User data" when launching the instance,
# or run it with sudo on a fresh instance. Edit the CHANGE-ME values first.
# The instance profile needs deploy/iam-policy.json (Bedrock + read access to the data prefix).
set -euxo pipefail

REPO_URL="https://github.com/nylla8444/puuurrrcrammers-hackathon-reed-elsevier.git"
SOURCE_S3_URI=""                                   # alternative to git: s3://bucket/path/source.zip
DATA_S3_URI="s3://CHANGE-ME-BUCKET/auditor-data"   # folders from scripts/extract_data.py
REGION="ap-southeast-2"
MODEL="global.anthropic.claude-opus-4-6-v1"
BASIC_AUTH="demo:CHANGE-ME"                        # browser login for the demo; empty = no login

dnf install -y docker git unzip
systemctl enable --now docker

rm -rf /opt/auditor
if [ -n "$SOURCE_S3_URI" ]; then
  aws s3 cp "$SOURCE_S3_URI" /tmp/source.zip && unzip -q /tmp/source.zip -d /opt/auditor
else
  git clone --depth 1 "$REPO_URL" /opt/auditor
fi
cd /opt/auditor
docker build -t clairoscope .

mkdir -p /opt/auditor-state && chown 10001 /opt/auditor-state
docker rm -f auditor 2>/dev/null || true
# --network host lets the container use the instance role (IMDSv2 hop limit 1 blocks bridged containers)
docker run -d --name auditor --restart unless-stopped --network host \
  -e AWS_REGION="$REGION" -e AWS_DEFAULT_REGION="$REGION" \
  -e AUDITOR_LLM=bedrock -e AUDITOR_BEDROCK_API=invoke -e AUDITOR_BEDROCK_MODEL="$MODEL" -e AUDITOR_EFFORT=medium \
  -e AUDITOR_DATA_S3_URI="$DATA_S3_URI" -e AUDITOR_BASIC_AUTH="$BASIC_AUTH" \
  -v /opt/auditor-state:/app/state \
  clairoscope

echo "Clairoscope starting on port 8080 (check: curl localhost:8080/api/health)"
