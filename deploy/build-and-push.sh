#!/usr/bin/env bash
# Build the image and push it to ECR (for the ECS path). Run from the repo root on a machine with
# Docker + AWS CLI (an EC2 instance or CodeBuild works if your laptop has no Docker).
set -euo pipefail

REGION="${REGION:-ap-southeast-2}"
REPO="${REPO:-clairoscope}"
TAG="${TAG:-latest}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"

aws ecr describe-repositories --repository-names "$REPO" --region "$REGION" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "$REPO" --region "$REGION" --image-scanning-configuration scanOnPush=true
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"

docker build -t "$REPO:$TAG" .
docker tag "$REPO:$TAG" "$REGISTRY/$REPO:$TAG"
docker push "$REGISTRY/$REPO:$TAG"
echo "Pushed $REGISTRY/$REPO:$TAG"
